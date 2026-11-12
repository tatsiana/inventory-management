"""
Tests for restocking order API endpoints.
"""
from datetime import datetime

import pytest

import mock_data


@pytest.fixture(autouse=True)
def reset_restock_orders():
    """Restock orders live in a module-level list; isolate each test."""
    mock_data.restock_orders.clear()
    yield
    mock_data.restock_orders.clear()


@pytest.fixture
def forecasts_by_sku(client):
    """Demand forecasts keyed by SKU."""
    response = client.get("/api/demand")
    return {f["item_sku"]: f for f in response.json()}


class TestDemandForecastPricing:
    """Demand forecasts carry the fields the restocking tab needs."""

    def test_demand_forecasts_have_pricing_fields(self, client):
        """Test that every forecast has a unit cost, category and warehouse."""
        response = client.get("/api/demand")
        assert response.status_code == 200

        for forecast in response.json():
            assert isinstance(forecast["unit_cost"], (int, float))
            assert forecast["unit_cost"] > 0
            assert forecast["category"]
            assert forecast["warehouse"] in ["San Francisco", "London", "Tokyo"]


class TestRestockOrderEndpoints:
    """Test suite for restock order endpoints."""

    def test_get_restock_orders_empty(self, client):
        """Test that no restock orders exist initially."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restock_order(self, client, forecasts_by_sku):
        """Test creating a valid restock order."""
        payload = {
            "warehouse": "London",
            "budget": 10000,
            "items": [
                {"item_sku": "WDG-001", "quantity": 100},
                {"item_sku": "GSK-203", "quantity": 50},
            ],
        }
        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["id"] == "RST-0001"
        assert order["warehouse"] == "London"
        assert order["status"] == "Submitted"
        assert order["lead_time_days"] == 10
        assert order["budget"] == 10000
        assert len(order["items"]) == 2

        expected_total = sum(
            line["quantity"] * forecasts_by_sku[line["item_sku"]]["unit_cost"]
            for line in payload["items"]
        )
        assert abs(order["total_cost"] - expected_total) < 0.01

        for item in order["items"]:
            assert abs(item["line_total"] - item["quantity"] * item["unit_cost"]) < 0.01

    def test_restock_order_expected_delivery(self, client):
        """Test that expected delivery is created date plus the warehouse lead time."""
        for warehouse, days in [("San Francisco", 5), ("London", 10), ("Tokyo", 14)]:
            response = client.post("/api/restock-orders", json={
                "warehouse": warehouse,
                "budget": 1000,
                "items": [{"item_sku": "PSU-501", "quantity": 10}],
            })
            assert response.status_code == 201

            order = response.json()
            assert order["lead_time_days"] == days
            created = datetime.fromisoformat(order["created_date"])
            expected = datetime.fromisoformat(order["expected_delivery"])
            assert (expected - created).days == days

    def test_restock_order_uses_server_pricing(self, client, forecasts_by_sku):
        """Test that unit cost comes from the forecast, not the client."""
        response = client.post("/api/restock-orders", json={
            "warehouse": "Tokyo",
            "budget": 1000,
            "items": [{"item_sku": "PSU-501", "quantity": 10, "unit_cost": 0.01}],
        })
        assert response.status_code == 201
        assert response.json()["items"][0]["unit_cost"] == forecasts_by_sku["PSU-501"]["unit_cost"]

    def test_created_orders_listed_newest_first(self, client):
        """Test that submitted orders appear in GET, newest first."""
        for _ in range(2):
            client.post("/api/restock-orders", json={
                "warehouse": "Tokyo",
                "budget": 1000,
                "items": [{"item_sku": "PSU-501", "quantity": 1}],
            })

        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        ids = [order["id"] for order in response.json()]
        assert ids == ["RST-0002", "RST-0001"]

    def test_restock_order_over_budget(self, client):
        """Test that an order costing more than the budget is rejected."""
        response = client.post("/api/restock-orders", json={
            "warehouse": "London",
            "budget": 100,
            "items": [{"item_sku": "MTR-304", "quantity": 10}],
        })
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()
        assert client.get("/api/restock-orders").json() == []

    def test_restock_order_unknown_sku(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post("/api/restock-orders", json={
            "warehouse": "London",
            "budget": 1000,
            "items": [{"item_sku": "NOPE-999", "quantity": 1}],
        })
        assert response.status_code == 400
        assert "NOPE-999" in response.json()["detail"]

    def test_restock_order_unknown_warehouse(self, client):
        """Test that an unknown warehouse is rejected."""
        response = client.post("/api/restock-orders", json={
            "warehouse": "Atlantis",
            "budget": 1000,
            "items": [{"item_sku": "PSU-501", "quantity": 1}],
        })
        assert response.status_code == 400
        assert "warehouse" in response.json()["detail"].lower()

    def test_restock_order_empty_items(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restock-orders", json={
            "warehouse": "London",
            "budget": 1000,
            "items": [],
        })
        assert response.status_code == 400

    def test_restock_order_duplicate_skus(self, client):
        """Test that the same SKU twice in one order is rejected."""
        response = client.post("/api/restock-orders", json={
            "warehouse": "London",
            "budget": 1000,
            "items": [
                {"item_sku": "PSU-501", "quantity": 1},
                {"item_sku": "PSU-501", "quantity": 2},
            ],
        })
        assert response.status_code == 400

    @pytest.mark.parametrize("quantity", [0, -5])
    def test_restock_order_invalid_quantity(self, client, quantity):
        """Test that non-positive quantities fail validation."""
        response = client.post("/api/restock-orders", json={
            "warehouse": "London",
            "budget": 1000,
            "items": [{"item_sku": "PSU-501", "quantity": quantity}],
        })
        assert response.status_code == 422
