# State machines

`app/thikra/state_machine.py` defines DRAFT, MANDATE_COMPILED, MANDATE_CONFIRMED, AUTHORIZATION_PENDING, AUTHORIZED, PROVIDER_SELECTED, PAYMENT_INVOKED, PLANNING, GENERATING, COMPOSING, VERIFYING, HUMAN_REVIEW, ACCEPTED, RETRYING, REJECTED, REDRESS_OPEN, COMPLETED, FAILED, and CANCELLED transitions.

Launch enters PLANNING so prompts remain editable. Confirmation enters GENERATING. A retry must stay below the mandate, authorization, and retry count; cost is enforced server-side. Invalid transitions return stable conflict codes.

Payment state is independent: authorization is not settlement, credential availability is not invocation, receipt is not acceptance, and redress is not a refund.
