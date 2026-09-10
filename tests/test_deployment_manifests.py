"""Tests for the deployment manifests themselves.

Nothing here runs a container. These are the checks that catch the manifests
drifting away from the code they deploy — the class of mistake you otherwise
find on the node, in the dark, with a wedged stack.

Three things are worth pinning:

*Names.* Podman refuses to guess a registry for a short image name when there
is nobody to ask, and every worker is started by module path. A rename in the
code that does not reach the manifest fails at `podman-compose up`, minutes
after the build, and says only that a module was not found.

*Shape.* The autonomous worker and PostgreSQL are absent from the base stack
on purpose. That is a safety property, not a filing decision, and a helpful
future edit that "tidies them back in" should fail here.

*Defaults.* The values in `.env.example` are what a fresh deployment inherits.
For this application that includes whether it can place an order.
"""

from __future__ import annotations

import pathlib
import re
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml comes with the [app] extra
    yaml = None

REPO = pathlib.Path(__file__).resolve().parent.parent
INFRA = REPO / "infrastructure" / "local"
BASE = INFRA / "podman-compose.yml"
POSTGRES_OVERLAY = INFRA / "podman-compose.postgres.yml"
AUTONOMOUS_OVERLAY = INFRA / "podman-compose.autonomous.yml"
ENV_EXAMPLE = INFRA / ".env.example"
CONTAINERFILE = REPO / "Containerfile"


def _load(path):
    return yaml.safe_load(path.read_text())


def _resolve_defaults(value):
    """Substitute compose's `${NAME:-default}` with its default.

    Enough to check what the manifests say when nothing is exported, which is
    the case the provisioning script runs in.
    """
    value = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}", r"\1", value)
    return re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", "", value)


@unittest.skipIf(yaml is None, "pyyaml is not installed")
class ComposeFileTests(unittest.TestCase):
    def setUp(self):
        self.base = _load(BASE)
        self.postgres = _load(POSTGRES_OVERLAY)
        self.autonomous = _load(AUTONOMOUS_OVERLAY)

    # ── Shape ────────────────────────────────────────────────────────────────

    def test_the_base_stack_is_the_four_services_that_cannot_trade(self):
        self.assertEqual(
            set(self.base["services"]),
            {"migrate", "web", "evaluation-worker", "reconciliation-worker"},
        )

    def test_the_autonomous_worker_is_not_in_the_base_stack(self):
        """It is the only service that can place an order unattended, so
        starting it has to be a sentence somebody typed."""
        self.assertNotIn("autonomous-worker", self.base["services"])
        self.assertIn("autonomous-worker", self.autonomous["services"])

    def test_postgres_is_not_in_the_base_stack(self):
        """External by default: a second database is a second thing to patch,
        monitor and back up."""
        self.assertNotIn("postgres", self.base["services"])
        self.assertIn("postgres", self.postgres["services"])

    def test_nothing_starts_before_the_schema_is_current(self):
        """A worker on a schema older than its code writes rows nothing can
        read back, so every service waits for migrate to exit 0."""
        for name, service in self.base["services"].items():
            if name == "migrate":
                continue
            with self.subTest(service=name):
                self.assertEqual(
                    service["depends_on"]["migrate"]["condition"],
                    "service_completed_successfully",
                )

    def test_the_autonomous_worker_waits_for_it_too(self):
        service = self.autonomous["services"]["autonomous-worker"]

        self.assertEqual(
            service["depends_on"]["migrate"]["condition"],
            "service_completed_successfully",
        )

    def test_the_migration_waits_for_a_local_database_to_be_ready(self):
        service = self.postgres["services"]["migrate"]

        self.assertEqual(
            service["depends_on"]["postgres"]["condition"], "service_healthy"
        )

    # ── Names ────────────────────────────────────────────────────────────────

    def _services(self):
        for path, document in (
            (BASE, self.base),
            (POSTGRES_OVERLAY, self.postgres),
            (AUTONOMOUS_OVERLAY, self.autonomous),
        ):
            for name, service in document["services"].items():
                yield path.name, name, service

    def test_every_image_is_fully_qualified(self):
        """Podman will not guess a registry for a short name when there is
        nobody to ask, and a provisioning script has nobody to ask."""
        for source, name, service in self._services():
            image = service.get("image")
            if image is None:
                continue
            resolved = _resolve_defaults(image)
            registry = resolved.split("/", 1)[0]
            with self.subTest(source=source, service=name, image=resolved):
                self.assertTrue(
                    registry == "localhost" or "." in registry or ":" in registry,
                    f"{resolved} has no registry — podman would refuse it",
                )

    def test_every_worker_command_names_a_module_that_exists(self):
        import importlib.util

        for source, name, service in self._services():
            command = service.get("command")
            if not isinstance(command, list) or "-m" not in command:
                continue
            module = command[command.index("-m") + 1]
            with self.subTest(source=source, service=name, module=module):
                self.assertIsNotNone(
                    importlib.util.find_spec(module), f"{module} is not importable"
                )

    def test_the_database_url_reaches_every_service_by_one_mechanism(self):
        """Interpolated, never inherited from env_file — so `podman inspect`
        always answers "which database is it talking to"."""
        for source, name, service in self._services():
            # Overlay fragments that only add ordering carry no environment of
            # their own; the rule is about whole service definitions.
            if name == "postgres" or "image" not in service:
                continue
            with self.subTest(source=source, service=name):
                self.assertEqual(
                    service.get("environment", {}).get("DATABASE_URL"),
                    "${DATABASE_URL}",
                )

    def test_the_containerfile_base_images_are_fully_qualified_too(self):
        """Same rule as the compose images, and for the same reason: a short
        name resolves through whatever shortnames.conf the host ships."""
        for line in CONTAINERFILE.read_text().splitlines():
            if not line.startswith("FROM "):
                continue
            image = line.split()[1]
            registry = image.split("/", 1)[0]
            with self.subTest(image=image):
                self.assertTrue(
                    registry == "localhost" or "." in registry or ":" in registry,
                    f"{image} would resolve through an alias file",
                )

    def test_the_web_healthcheck_and_the_image_agree_on_the_path(self):
        from webui.utils.health import HEALTH_PATH

        probe = " ".join(self.base["services"]["web"]["healthcheck"]["test"])

        self.assertIn(HEALTH_PATH, probe)
        self.assertIn(HEALTH_PATH, CONTAINERFILE.read_text())

    def test_the_workers_are_probed_through_the_control_plane(self):
        """A wedged loop is still a live process; the heartbeat is the only
        thing that distinguishes them."""
        for name in ("evaluation-worker", "reconciliation-worker"):
            probe = self.base["services"][name]["healthcheck"]["test"]

            with self.subTest(service=name):
                self.assertIn("tradingagents.operations.control_plane", probe)
                self.assertIn(name, probe)


class EnvExampleTests(unittest.TestCase):
    """What a fresh deployment inherits before anybody edits anything."""

    def setUp(self):
        self.values = {}
        for line in ENV_EXAMPLE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            self.values[key.strip()] = value.strip()

    def test_it_cannot_trade_out_of_the_box(self):
        self.assertEqual(self.values["AUTONOMOUS_ENABLED"], "false")
        self.assertEqual(self.values["EXECUTION_GATEWAY"], "dry-run")

    def test_the_broker_defaults_to_the_paper_account(self):
        self.assertEqual(self.values["ALPACA_USE_PAPER"], "True")

    def test_no_credential_is_filled_in(self):
        for key, value in self.values.items():
            if key.endswith(("_API_KEY", "_SECRET_KEY", "_TOKEN")):
                with self.subTest(key=key):
                    self.assertEqual(value, "", f"{key} ships with a value")

    def test_the_workbench_is_on_loopback_by_default(self):
        """There is no authentication in front of it. The Proxmox script is
        what widens this, having put an LXC boundary around it first."""
        self.assertEqual(self.values["HOST_BIND"], "127.0.0.1")


class BuildContextTests(unittest.TestCase):
    """What `COPY . .` is allowed to put in the image."""

    def setUp(self):
        self.rules = [
            line.strip()
            for line in (REPO / ".containerignore").read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_the_deployment_env_file_cannot_reach_a_layer(self):
        """It holds live broker credentials and it is not at the repo root,
        so a bare `.env` rule — which matches only the root — would miss it
        and bake the keys into every image."""
        self.assertIn("**/.env", self.rules)

    def test_nothing_re_includes_an_env_file(self):
        for rule in self.rules:
            with self.subTest(rule=rule):
                self.assertFalse(
                    rule.startswith("!") and "env" in rule,
                    f"{rule} re-includes an env file into the build context",
                )

    def test_the_virtualenv_and_caches_stay_out(self):
        for rule in (".venv", "**/__pycache__", ".git"):
            self.assertIn(rule, self.rules)


class ProxmoxScriptTests(unittest.TestCase):
    """The parts of the provisioning script that are load-bearing."""

    def setUp(self):
        self.script = (REPO / "infrastructure" / "proxmox" / "tradingagents.sh").read_text()

    def test_the_lxc_gets_the_features_podman_needs(self):
        """nesting for containers-in-a-container, keyctl for crun's keyring,
        fuse for the overlay driver. Without them podman fails in ways that
        do not name the cause."""
        self.assertIn("--features nesting=1,keyctl=1,fuse=1", self.script)

    def test_the_container_stays_unprivileged(self):
        self.assertIn("--unprivileged 1", self.script)

    def test_the_lxc_is_given_a_tun_device(self):
        """pasta and slirp4netns both build a container's network by making
        a tap device in its namespace, and an unprivileged LXC has no
        /dev/net/tun. Without it podman reports `pasta failed with exit code
        -1:` and nothing after the colon."""
        self.assertIn("lxc.mount.entry: /dev/net/tun", self.script)
        self.assertIn("lxc.cgroup2.devices.allow: c 10:200 rwm", self.script)

    def test_a_two_line_build_is_probed_before_the_real_one(self):
        """Three attempts died at STEP 4 of 7, minutes in. The probe is a
        build rather than a `podman run` because those are different code
        paths — `podman run` took the default bridge and passed while a RUN
        step asked buildah for a namespace and failed."""
        probe = self.script.index("probing how the build reaches the network")
        build = self.script.index("building the application image")

        self.assertLess(probe, build)
        self.assertIn("RUN true", self.script)

    def test_the_probe_chooses_the_build_network_and_records_it(self):
        """Finding out that the default fails is only half of it; the answer
        has to reach the real build, and survive into a later `make build`
        run by hand inside the LXC."""
        self.assertIn('--network=host', self.script)
        self.assertIn('set_env_key BUILD_NETWORK', self.script)
        self.assertIn('BUILD_NETWORK="$BUILD_NETWORK" build', self.script)

    def test_podmans_runtime_helpers_are_named_explicitly(self):
        """`--no-install-recommends` plus podman is a trap: on Ubuntu these
        are Recommends, so podman installs cleanly and then fails at the
        moment of use with an error naming a binary rather than a package.

        pasta broke `podman build`; catatonit is what `init: true` runs as
        pid 1; aardvark-dns is how `postgres` resolves on the user-defined
        network in POSTGRES_MODE=local.
        """
        for package in ("passt", "netavark", "aardvark-dns", "catatonit", "crun"):
            with self.subTest(package=package):
                self.assertIn(package, self.script)

    def test_the_runtime_helpers_are_verified_before_the_build(self):
        """Twenty minutes into an image build is the wrong place to discover
        that the network helper is absent."""
        install = self.script.index("apt-get install")
        check = self.script.index("podman runtime helpers present")
        build = self.script.index("building the application image")

        self.assertLess(install, check)
        self.assertLess(check, build)

    def test_pct_exec_does_not_inherit_a_locale_the_container_lacks(self):
        """The node's LANG reaches a container with no locales generated, and
        the perl warnings that follow bury the output that matters."""
        self.assertIn("LC_ALL=C", self.script)

    def test_the_compose_ordering_capability_is_probed_not_assumed(self):
        """Older podman-compose accepts `service_completed_successfully` and
        ignores it — the stack starts, looks fine, and no longer guarantees
        that migrations finish first. A distribution's package version is not
        something to bet that on, so the script checks and installs its own
        if the packaged one cannot do it."""
        self.assertIn("service_completed_successfully", self.script)
        self.assertIn("/opt/podman-compose", self.script)
        self.assertIn("podman-compose>=1.2", self.script)

    def test_the_template_is_discoverable_when_the_pinned_name_is_stale(self):
        """Point-release suffixes move; a hard-coded filename that is no
        longer offered should not be the end of the run."""
        self.assertIn("TEMPLATE_MATCH", self.script)
        self.assertIn("pveam available", self.script)

    def test_the_autonomous_worker_is_off_by_default(self):
        self.assertIn('AUTONOMOUS="${AUTONOMOUS:-0}"', self.script)

    def test_it_never_overwrites_an_existing_env_file(self):
        """A re-run that reset live broker credentials to blanks is the worst
        thing something advertised as idempotent could do."""
        self.assertRegex(
            self.script,
            re.compile(r"if pct exec .*test -f .*ENV_FILE.*\n\s*msg_ok"),
        )

    def test_both_scripts_stop_on_the_first_error(self):
        for name in ("tradingagents.sh", "postgres-database.sh"):
            script = (REPO / "infrastructure" / "proxmox" / name).read_text()
            with self.subTest(script=name):
                self.assertIn("set -euo pipefail", script)

    def test_the_database_script_catches_a_misspelt_ctid(self):
        """`PGCTID=102` silently took the network path and then failed
        several steps later complaining about psql, which points at the
        wrong problem. The variable is PG_CTID; a near miss says so."""
        script = (REPO / "infrastructure" / "proxmox" / "postgres-database.sh").read_text()

        self.assertIn("PGCTID", script)
        self.assertIn("the variable is PG_CTID", script)

    def test_the_database_script_never_drops_anything(self):
        script = (REPO / "infrastructure" / "proxmox" / "postgres-database.sh").read_text()

        for statement in ("DROP DATABASE", "DROP ROLE", "DROP SCHEMA"):
            with self.subTest(statement=statement):
                self.assertNotIn(statement, script)


if __name__ == "__main__":
    unittest.main()
