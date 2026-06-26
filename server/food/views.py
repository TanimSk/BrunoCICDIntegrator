from django.db.models import Q

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from utils.shared import StandardResultsSetPagination

from .models import Food
from .serializers import FoodSerializer


class FoodListCreateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        foods = Food.objects.all()
        search = request.query_params.get("search")

        if search:
            foods = foods.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(category__icontains=search)
            )

        price_order = request.query_params.get("price_order")

        if price_order == "asc":
            foods = foods.order_by("price")
        elif price_order == "desc":
            foods = foods.order_by("-price")

        paginator = StandardResultsSetPagination()
        paginated_foods = paginator.paginate_queryset(foods, request)
        serializer = FoodSerializer(paginated_foods, many=True)

        return Response(
            {
                "success": True,
                "data": {
                    "count": paginator.page.paginator.count,
                    "page_size": paginator.get_page_size(request),
                    "next": paginator.get_next_link(),
                    "previous": paginator.get_previous_link(),
                    "num_pages": paginator.page.paginator.num_pages,
                    "current_page": paginator.page.number,
                    "results": serializer.data,
                },
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = FoodSerializer(data=request.data)

        if serializer.is_valid(raise_exception=True):
            serializer.save()

            return Response(
                {
                    "success": True,
                    "message": "Food created successfully.",
                    "data": serializer.data,
                },
                status=status.HTTP_201_CREATED,
            )


class FoodDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_object(self, pk):
        try:
            return Food.objects.get(pk=pk)
        except Food.DoesNotExist:
            return None

    def get(self, request, pk):
        food = self.get_object(pk)

        if food is None:
            return Response(
                {
                    "success": False,
                    "message": "Food not found.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = FoodSerializer(food)

        return Response(
            {
                "success": True,
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def put(self, request, pk):
        food = self.get_object(pk)

        if food is None:
            return Response(
                {
                    "success": False,
                    "message": "Food not found.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = FoodSerializer(food, data=request.data)

        if serializer.is_valid(raise_exception=True):
            serializer.save()

            return Response(
                {
                    "success": True,
                    "message": "Food updated successfully.",
                    "data": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

    def patch(self, request, pk):
        food = self.get_object(pk)

        if food is None:
            return Response(
                {
                    "success": False,
                    "message": "Food not found.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = FoodSerializer(food, data=request.data, partial=True)

        if serializer.is_valid(raise_exception=True):
            serializer.save()

            return Response(
                {
                    "success": True,
                    "message": "Food updated successfully.",
                    "data": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

    def delete(self, request, pk):
        food = self.get_object(pk)

        if food is None:
            return Response(
                {
                    "success": False,
                    "message": "Food not found.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        food.delete()

        return Response(
            {
                "success": True,
                "message": "Food deleted successfully.",
            },
            status=status.HTTP_200_OK,
        )
