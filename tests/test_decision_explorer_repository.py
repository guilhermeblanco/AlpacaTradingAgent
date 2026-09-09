import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradingagents.persistence.postgres.models import (
    Base,
    BrokerFillRow,
    BrokerOrderRow,
    LifecycleRow,
    LifecycleTransitionRow,
)
from tradingagents.persistence.postgres.repositories import PostgresDecisionExplorerRepository


class DecisionExplorerRepositoryTests(unittest.TestCase):
    def test_joins_lifecycle_order_and_fill_timeline(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine)
        now = datetime.now(timezone.utc)
        with factory() as session:
            session.add(
                LifecycleRow(
                    decision_id="d1",
                    symbol="AAPL",
                    status="filled",
                    idempotency_key="ata-d1",
                    created_at=now,
                    updated_at=now,
                    metadata_payload={},
                )
            )
            session.add(
                LifecycleTransitionRow(
                    decision_id="d1",
                    recorded_at=now,
                    from_status="submitted",
                    to_status="filled",
                    payload={},
                )
            )
            session.add(
                BrokerOrderRow(
                    order_key="o1",
                    decision_id="d1",
                    leg_index=0,
                    broker="alpaca",
                    broker_order_id="remote-1",
                    client_order_id="ata-d1-0",
                    symbol="AAPL",
                    side="buy",
                    status="filled",
                    requested_quantity=2,
                    filled_quantity=2,
                    filled_avg_price=100,
                    submitted_at=now,
                    updated_at=now,
                    raw={},
                )
            )
            session.add(
                BrokerFillRow(
                    fill_key="f1",
                    order_key="o1",
                    fill_sequence=1,
                    quantity=2,
                    price=100,
                    observed_at=now,
                    source="broker",
                )
            )
            session.commit()

            repository = PostgresDecisionExplorerRepository(session)
            summary = repository.list_decisions()[0]
            detail = repository.get_decision("d1")

        self.assertEqual(summary.order_count, 1)
        self.assertEqual(summary.filled_quantity, 2)
        self.assertEqual(
            {event.category for event in detail.timeline},
            {"lifecycle", "order", "fill"},
        )


if __name__ == "__main__":
    unittest.main()
