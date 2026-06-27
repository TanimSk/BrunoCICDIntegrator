from django.urls import path

from .views import CategoryAPIView, FoodDetailView, FoodListCreateView


urlpatterns = [
    path("", FoodListCreateView.as_view(), name="food-list-create"),
    path("catagory/", CategoryAPIView.as_view(), name="food-catagory-view"),
    path("<int:pk>/", FoodDetailView.as_view(), name="food-detail"),
]
