from django.urls import path

from .views import FoodDetailView, FoodListCreateView


urlpatterns = [
    path("", FoodListCreateView.as_view(), name="food-list-create"),
    path("<int:pk>/", FoodDetailView.as_view(), name="food-detail"),
]
