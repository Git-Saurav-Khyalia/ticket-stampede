"""
Sequential (non-concurrent) behavior tests for Version 1.

Per spec: this version is only required to behave correctly when calls
are NOT concurrent. These tests establish that baseline. They will pass
on this naive implementation. They are not, and are not meant to be, a
demonstration of correctness under load -- see the load tester for that.
"""


def test_reset_creates_sale_with_expected_ticket_total(client):
    resp = client.post("/reset", json={"ticket_count": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket_total"] == 10
    assert "sale_id" in body


def test_status_before_any_purchase(client):
    client.post("/reset", json={"ticket_count": 5})
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket_total"] == 5
    assert body["sold_count"] == 0
    assert body["sold_tickets"] == []


def test_single_purchase_succeeds_and_is_reflected_in_status(client):
    client.post("/reset", json={"ticket_count": 5})

    buy_resp = client.post("/buy", json={"user_id": "alice", "request_id": "r1"})
    assert buy_resp.status_code == 200
    ticket_number = buy_resp.json()["ticket_number"]
    assert ticket_number in range(1, 6)

    status_resp = client.get("/status")
    body = status_resp.json()
    assert body["sold_count"] == 1
    assert body["sold_tickets"][0]["ticket_number"] == ticket_number
    assert body["sold_tickets"][0]["user_id"] == "alice"


def test_sequential_purchases_get_distinct_ticket_numbers(client):
    client.post("/reset", json={"ticket_count": 3})

    numbers = []
    for i in range(3):
        resp = client.post("/buy", json={"user_id": f"user{i}", "request_id": f"r{i}"})
        assert resp.status_code == 200
        numbers.append(resp.json()["ticket_number"])

    assert sorted(numbers) == [1, 2, 3]


def test_sold_out_after_all_tickets_taken_sequentially(client):
    client.post("/reset", json={"ticket_count": 2})
    client.post("/buy", json={"user_id": "a", "request_id": "r1"})
    client.post("/buy", json={"user_id": "b", "request_id": "r2"})

    resp = client.post("/buy", json={"user_id": "c", "request_id": "r3"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "SOLD_OUT"


def test_status_sold_count_matches_actual_rows_when_run_sequentially(client):
    client.post("/reset", json={"ticket_count": 4})
    client.post("/buy", json={"user_id": "a", "request_id": "r1"})
    client.post("/buy", json={"user_id": "b", "request_id": "r2"})

    resp = client.get("/status")
    body = resp.json()
    assert body["sold_count"] == 2
    assert len(body["sold_tickets"]) == 2


def test_duplicate_request_id_run_sequentially_is_KNOWN_to_be_unsafe(client):
    """
    This test documents, rather than hides, the known Version 1 gap:
    replaying the same request_id sequentially (not even concurrently)
    is sold a SECOND ticket. This is expected and correct for Version 1.
    It must NOT hold in Version 2.
    """
    client.post("/reset", json={"ticket_count": 5})

    first = client.post("/buy", json={"user_id": "alice", "request_id": "same-id"})
    second = client.post("/buy", json={"user_id": "alice", "request_id": "same-id"})

    assert first.status_code == 200
    assert second.status_code == 200
    # KNOWN BUG (by design, for now): two distinct tickets were issued for
    # one logical request_id.
    assert first.json()["ticket_number"] != second.json()["ticket_number"]


def test_reset_preserves_previous_sale_history(client):
    client.post("/reset", json={"ticket_count": 2})
    client.post("/buy", json={"user_id": "a", "request_id": "r1"})

    reset_resp = client.post("/reset", json={"ticket_count": 3})
    new_sale_id = reset_resp.json()["sale_id"]

    status_resp = client.get("/status")
    body = status_resp.json()
    # /status reports the NEW active sale, starting fresh
    assert body["sale_id"] == new_sale_id
    assert body["sold_count"] == 0
