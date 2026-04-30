"""Regression tests for Floor plan_image cleanup (TC-15).

Verifies that orphan files are deleted when:
- A floor's plan_image is replaced via PATCH
- A floor with an image is deleted
"""
import io

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.services.models import Floor
from apps.users.models import User


FLOORS_URL = '/api/v1/services/floors/'


def floor_detail_url(floor_id):
    return f'{FLOORS_URL}{floor_id}/'


def make_image_file(name='plan.jpg'):
    """Create a minimal in-memory JPEG suitable for ImageField upload."""
    img = Image.new('RGB', (10, 10), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type='image/jpeg')


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@cleanup.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def authed_client(api_client, superadmin):
    api_client.force_authenticate(user=superadmin)
    return api_client


@pytest.mark.django_db
class TestFloorImageReplacementCleanup:
    """PATCH with a new image should delete the old file from storage."""

    def test_old_image_deleted_on_update(self, authed_client):
        # Create a floor with an image
        image1 = make_image_file('plan_old.jpg')
        resp = authed_client.post(
            FLOORS_URL,
            {'number': 1, 'name': 'Floor 1', 'plan_image': image1},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        floor_id = resp.data['id']

        floor = Floor.objects.get(pk=floor_id)
        old_image_path = floor.plan_image.name
        assert old_image_path
        assert default_storage.exists(old_image_path)

        # Update with a new image
        image2 = make_image_file('plan_new.jpg')
        resp = authed_client.patch(
            floor_detail_url(floor_id),
            {'plan_image': image2},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_200_OK

        floor.refresh_from_db()
        new_image_path = floor.plan_image.name
        assert new_image_path != old_image_path

        # Old file should be deleted, new file should exist
        assert not default_storage.exists(old_image_path), (
            f'Orphan file not cleaned up: {old_image_path}'
        )
        assert default_storage.exists(new_image_path)

        # Cleanup
        default_storage.delete(new_image_path)

    def test_no_deletion_when_image_unchanged(self, authed_client):
        # Create a floor with an image
        image = make_image_file('plan_keep.jpg')
        resp = authed_client.post(
            FLOORS_URL,
            {'number': 2, 'name': 'Floor 2', 'plan_image': image},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        floor_id = resp.data['id']

        floor = Floor.objects.get(pk=floor_id)
        image_path = floor.plan_image.name

        # PATCH without changing the image
        resp = authed_client.patch(
            floor_detail_url(floor_id),
            {'name': 'Renamed Floor'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_200_OK

        # Image file should still exist
        assert default_storage.exists(image_path)

        # Cleanup
        default_storage.delete(image_path)


@pytest.mark.django_db
class TestFloorImageDeleteCleanup:
    """DELETE floor should remove the associated image file from storage."""

    def test_image_deleted_on_floor_destroy(self, authed_client):
        # Create a floor with an image
        image = make_image_file('plan_destroy.jpg')
        resp = authed_client.post(
            FLOORS_URL,
            {'number': 3, 'name': 'Floor 3', 'plan_image': image},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        floor_id = resp.data['id']

        floor = Floor.objects.get(pk=floor_id)
        image_path = floor.plan_image.name
        assert default_storage.exists(image_path)

        # Delete the floor
        resp = authed_client.delete(floor_detail_url(floor_id))
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        # Image file should be cleaned up
        assert not default_storage.exists(image_path), (
            f'File not deleted on floor destroy: {image_path}'
        )

    def test_destroy_floor_without_image(self, authed_client):
        # Create a floor without an image
        resp = authed_client.post(
            FLOORS_URL,
            {'number': 4, 'name': 'No Image Floor'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        floor_id = resp.data['id']

        # Should not raise when deleting a floor with no image
        resp = authed_client.delete(floor_detail_url(floor_id))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
