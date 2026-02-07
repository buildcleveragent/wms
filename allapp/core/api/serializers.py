# allapp/core/api/serializers.py
from rest_framework import serializers
class BaseModelSerializer(serializers.ModelSerializer):
    read_only_fields = ("id","created_at","created_by","updated_at","updated_by")
