# Uncertain broker submissions

A transport failure after an order request is sent does not prove that the
broker rejected it. Retrying immediately can create a duplicate position.

Every execution leg carries a stable broker client order ID. When Alpaca,
Tradier, or Robinhood times out or loses its connection during submission, the
pipeline now:

1. records the leg with status `unknown` and no assumed broker order ID;
2. keeps the lifecycle in `submitted` and retains its portfolio reservation;
3. enqueues normal reconciliation using the client order ID;
4. returns `submission_uncertain=true` to the operator; and
5. returns the same durable result if the decision is dispatched again.

The reconciliation worker searches broker order history by client order ID. If
the order appears, normal fill tracking continues. If it is not yet visible or
the broker remains unavailable, the task is rescheduled without resubmitting
the order.

Alpaca close intents use an ordinary opposite-side quantity order with the same
client order ID mechanism. The broker's convenience `close_position` endpoint
is not used by the autonomous execution gateway because that endpoint does not
provide the required idempotency identity.

An uncertain submission is not counted as a broker rejection. It remains
visible as reconciliation backlog and continues to reserve exposure until a
terminal broker state is observed.
