from django.urls import path
from .views import CloudDataIngestView, IntegrityMetricsView

urlpatterns = [
    path("cloud-data/ingest/", CloudDataIngestView.as_view(), name="data_ingest"),
    path("metrics/integrity/", IntegrityMetricsView.as_view(), name="integrity_metrics"),
]
