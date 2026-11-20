from resona_client.webhooks import InvalidWebhookSignature, verify

seen_ids: set[str] = set()  # keep these in your database in production


def handle_webhook(headers: dict[str, str], body: bytes, secret: str) -> int:
    """Handle one POST from the hub; return the HTTP status to answer with.

    ``headers`` has lowercase names, ``body`` is the raw request body.
    """
    try:
        event = verify(headers, body, secret)  # signature, freshness, payload shape
    except InvalidWebhookSignature:
        return 401
    delivery_id = headers["webhook-id"]  # the same on every retry of this event
    if delivery_id in seen_ids:
        return 200  # already handled: answer 2xx so the hub stops retrying
    seen_ids.add(delivery_id)
    print(event.type, event.job_id, event.error_code)  # then fetch GET /v1/jobs/{id}/result
    return 200
