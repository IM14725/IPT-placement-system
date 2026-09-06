from rest_framework import serializers
from .models import Payment, PaymentMethod
from apps.applications.models import Application

class InitiatePaymentSerializer(serializers.Serializer):
    application_id = serializers.UUIDField()
    phone_number = serializers.CharField(max_length=20)
    network = serializers.ChoiceField(choices=PaymentMethod.choices)

    def validate_phone_number(self, value):
        # Enforce Tanzanian international format (255XXXXXXXXX)
        if value.startswith('0'):
            value = '255' + value[1:]
        if not value.startswith('255') or len(value) != 12 or not value.isdigit():
            raise serializers.ValidationError("Phone number must be in the format 2557XXXXXXXX")
        return value

    def validate_application_id(self, value):
        user = self.context['request'].user
        try:
            # Verify the application exists and belongs to the authenticated student
            application = Application.objects.get(id=value, student__user=user)
        except Application.DoesNotExist:
            raise serializers.ValidationError("Invalid application ID or permission denied.")
        
        if application.is_paid:
            raise serializers.ValidationError("This application has already been paid for.")
        
        return value

