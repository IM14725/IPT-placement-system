from django.db import models


class Region(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class District(models.Model):
    name = models.CharField(max_length=140)
    region = models.ForeignKey(
        Region, on_delete=models.CASCADE, related_name="districts"
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "region"], name="uniq_district_region")
        ]

    def __str__(self):
        return f"{self.name}, {self.region.name}"


class Ward(models.Model):
    name = models.CharField(max_length=160)
    district = models.ForeignKey(
        District, on_delete=models.CASCADE, related_name="wards"
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "district"], name="uniq_ward_district")
        ]

    def __str__(self):
        return f"{self.name}, {self.district.name}"