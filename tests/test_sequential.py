"""
Sequential (non-concurrent) behavior tests for the seller.
 
These tests exercise the /reset, /buy, /status contract without any
concurrency. Version 1's tests here already passed at this level (its
bugs only appeared under concurrency or under a repeated request_id --
see the note on duplicate requests below). Version 2 must continue to
pass all of these, plus behave correctly for the one case Version 1
deliberately got wrong: a repeated request_id.
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
 
 
def test_duplicate_request_id_run_sequentially_returns_the_original_ticket(client):
    """
    Version 1 sold a SECOND ticket here (documented, at the time, as a
    known bug -- see Experiment 2 in app/service.py's module docstring).
    Version 2 must not: a repeated request_id, even with no concurrency
    involved, must resolve to the ticket already issued for it, and must
    not consume a second ticket from the pool.
    """
    client.post("/reset", json={"ticket_count": 5})
 
    first = client.post("/buy", json={"user_id": "alice", "request_id": "same-id"})
    second = client.post("/buy", json={"user_id": "alice-retry", "request_id": "same-id"})
 
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["ticket_number"] == second.json()["ticket_number"]
 
    # Only one ticket was actually consumed from the pool.
    status_resp = client.get("/status")
    assert status_resp.json()["sold_count"] == 1
 
 
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
 