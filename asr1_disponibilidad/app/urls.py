from django.urls import path
from .views import HealthCheckView, AvailabilityMetricsView, MonthlyReportView
from asr2_integridad.app.views import DataIngestView, IntegrityMetricsView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("metrics/availability/", AvailabilityMetricsView.as_view(), name="availability_metrics"),
    path("reports/monthly/", MonthlyReportView.as_view(), name="monthly_report"),
    path("cloud-data/ingest/", DataIngestView.as_view(), name="data_ingest"),
    path("metrics/integrity/", IntegrityMetricsView.as_view(), name="integrity_metrics"),
]
