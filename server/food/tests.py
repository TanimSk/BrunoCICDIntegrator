from django.test import TestCase
from rest_framework.test import APIClient

from .models import Food


class FoodListCreateViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_get_food_list_returns_analysis_for_total_and_available_calories(self):
        Food.objects.create(
            name="Veggie Burger",
            description="Vegetarian burger",
            category="Fast Food",
            price="7.99",
            calories=450,
            is_available=True,
        )
        Food.objects.create(
            name="Chicken Wrap",
            description="Grilled chicken wrap",
            category="Wrap",
            price="6.50",
            calories=320,
            is_available=True,
        )
        Food.objects.create(
            name="Seasonal Special",
            description="Limited menu item",
            category="Special",
            price="9.00",
            calories=700,
            is_available=False,
        )

        response = self.client.get("/food/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["count"], 3)
        self.assertEqual(
            response.data["data"]["analysis"],
            {
                "total_food": 3,
                "available_food_calories_sum": 770,
            },
        )
