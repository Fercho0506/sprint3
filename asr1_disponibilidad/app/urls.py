from django.urls import path
from .views import HealthCheckView, AvailabilityMetricsView, MonthlyReportView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("metrics/availability/", AvailabilityMetricsView.as_view(), name="availability_metrics"),
    path("reports/monthly/", MonthlyReportView.as_view(), name="monthly_report"),
    path("", include("asr2_integridad.app.urls")),
]
