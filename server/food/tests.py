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

    def test_category_view_returns_unique_catagories_for_view_param(self):
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
            name="Another Burger",
            description="Burger variant",
            category="Fast Food",
            price="8.50",
            calories=500,
            is_available=True,
        )

        response = self.client.get("/food/catagory/?view=catagory")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["catagories"], ["Fast Food", "Wrap"])

    def test_category_view_returns_foods_by_catagory_name(self):
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

        response = self.client.get("/food/catagory/?catagory=fast food")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["catagory"], "fast food")
        self.assertEqual(response.data["data"]["count"], 1)
        self.assertEqual(response.data["data"]["results"][0]["name"], "Veggie Burger")

    def test_food_of_day_returns_404_when_no_food_exists(self):
        response = self.client.get("/food/food-of-day/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["message"], "No food items found.")

    def test_food_of_day_returns_a_random_food_item(self):
        first_food = Food.objects.create(
            name="Veggie Burger",
            description="Vegetarian burger",
            category="Fast Food",
            price="7.99",
            calories=450,
            is_available=True,
        )
        second_food = Food.objects.create(
            name="Chicken Wrap",
            description="Grilled chicken wrap",
            category="Wrap",
            price="6.50",
            calories=320,
            is_available=True,
        )

        response = self.client.get("/food/food-of-day/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["message"], "Food of the day selected successfully.")
        self.assertIn(response.data["data"]["id"], {first_food.id, second_food.id})
