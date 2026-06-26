from django.contrib import admin

from .models import Food


@admin.register(Food)
class FoodAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "price", "calories", "is_available"]
    list_filter = ["is_available", "category"]
    search_fields = ["name", "description", "category"]
