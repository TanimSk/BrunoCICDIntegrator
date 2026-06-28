from django.urls import path

from .views import CategoryAPIView, FoodDetailView, FoodListCreateView, FoodOfDayAPIView


urlpatterns = [
    path("", FoodListCreateView.as_view(), name="food-list-create"),
    path("food-of-day/", FoodOfDayAPIView.as_view(), name="food-of-day"),
    path("catagory/", CategoryAPIView.as_view(), name="food-catagory-view"),
    path("<int:pk>/", FoodDetailView.as_view(), name="food-detail"),
]
