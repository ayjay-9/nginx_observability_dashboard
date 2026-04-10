from django.urls import path
from . import views

app_name = "nginx_dashboard"
urlpatterns = [
    path("", views.index, name="index"),
    path("api/metrics/", views.api_metrics, name="api_metrics"),
    path("api/geoip/", views.api_geoip, name="api_geoip"),
    path("export/csv/", views.export_csv, name="export_csv"),
]
