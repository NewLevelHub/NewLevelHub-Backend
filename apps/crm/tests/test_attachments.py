"""
Integration tests for TaskAttachment endpoints:
  GET    /api/v1/crm/tasks/<task_pk>/attachments/
  POST   /api/v1/crm/tasks/<task_pk>/attachments/
  DELETE /api/v1/crm/tasks/<task_pk>/attachments/<pk>/
"""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Task, TaskAttachment
from apps.storage.models import File as StorageFile, Folder
from apps.users.models import User


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def attachments_url(task_pk):
    return f'/api/v1/crm/tasks/{task_pk}/attachments/'


def attachment_detail_url(task_pk, attachment_pk):
    return f'/api/v1/crm/tasks/{task_pk}/attachments/{attachment_pk}/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Company A', plan='standard', max_boards=10)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Company B', plan='standard', max_boards=10)


@pytest.fixture
def admin_a(db, company_a):
    return User.objects.create_user(
        email='admin_a@test.com',
        password='pass',
        first_name='Admin',
        last_name='A',
        role='company_admin',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='employee_a@test.com',
        password='pass',
        first_name='Employee',
        last_name='A',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_b(db, company_b):
    return User.objects.create_user(
        email='employee_b@test.com',
        password='pass',
        first_name='Employee',
        last_name='B',
        role='employee',
        company=company_b,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@test.com',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        company=None,
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def board_a(db, company_a, admin_a):
    return Board.objects.create(name='Board A', company=company_a, created_by=admin_a)


@pytest.fixture
def column_a(db, board_a):
    return Column.objects.create(board=board_a, name='To Do', position=1)


@pytest.fixture
def task_a(db, column_a, admin_a):
    return Task.objects.create(column=column_a, title='Task A', created_by=admin_a)


@pytest.fixture
def pdf_file():
    return SimpleUploadedFile(
        'report.pdf',
        b'%PDF-1.4 fake content',
        content_type='application/pdf',
    )


@pytest.fixture
def attachment_a(db, task_a, admin_a, pdf_file):
    return TaskAttachment.objects.create(
        task=task_a,
        file=pdf_file,
        filename='report.pdf',
        file_size=len(b'%PDF-1.4 fake content'),
        mime_type='application/pdf',
        uploaded_by=admin_a,
    )


@pytest.fixture
def storage_file_a(db, company_a, admin_a):
    folder = Folder.objects.create(name='Docs', owner=admin_a, company=company_a)
    content = b'storage file content'
    return StorageFile.objects.create(
        name='storage_doc.pdf',
        file=SimpleUploadedFile('storage_doc.pdf', content, content_type='application/pdf'),
        file_size=len(content),
        content_type='application/pdf',
        folder=folder,
        owner=admin_a,
        company=company_a,
    )


# ---------------------------------------------------------------------------
# AC 2: GET /api/v1/crm/tasks/<task_pk>/attachments/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAttachmentList:
    def test_company_member_can_list_attachments(self, api_client, employee_a, task_a, attachment_a):
        api_client.force_authenticate(employee_a)
        resp = api_client.get(attachments_url(task_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        item = data[0]
        assert item['filename'] == 'report.pdf'
        assert item['size'] == attachment_a.file_size
        assert item['mime_type'] == 'application/pdf'
        assert 'url' in item
        assert 'uploaded_by' in item
        assert 'created_at' in item

    def test_uploaded_by_nested_shape(self, api_client, admin_a, task_a, attachment_a):
        api_client.force_authenticate(admin_a)
        resp = api_client.get(attachments_url(task_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        uploaded_by = resp.json()[0]['uploaded_by']
        assert 'id' in uploaded_by
        assert 'full_name' in uploaded_by
        assert 'avatar' in uploaded_by

    def test_unauthenticated_gets_401(self, api_client, task_a):
        resp = api_client.get(attachments_url(task_a.pk))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_gets_403(self, api_client, guest_user, task_a):
        api_client.force_authenticate(guest_user)
        resp = api_client.get(attachments_url(task_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_other_company_employee_gets_403(self, api_client, employee_b, task_a):
        api_client.force_authenticate(employee_b)
        resp = api_client.get(attachments_url(task_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_can_list(self, api_client, superadmin, task_a, attachment_a):
        api_client.force_authenticate(superadmin)
        resp = api_client.get(attachments_url(task_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.json()) == 1

    def test_nonexistent_task_gets_404(self, api_client, employee_a):
        api_client.force_authenticate(employee_a)
        resp = api_client.get(attachments_url(99999))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# AC 1 Mode A: POST with direct file upload
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAttachmentDirectUpload:
    def test_employee_can_upload_pdf(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        f = SimpleUploadedFile('doc.pdf', b'%PDF-1.4 content', content_type='application/pdf')
        resp = api_client.post(
            attachments_url(task_a.pk),
            data={'file': f},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.json()
        assert data['filename'] == 'doc.pdf'
        assert data['mime_type'] == 'application/pdf'
        assert data['size'] > 0
        assert TaskAttachment.objects.filter(task=task_a).count() == 1

    def test_upload_png_is_allowed(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        f = SimpleUploadedFile('image.png', b'\x89PNG\r\n', content_type='image/png')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_upload_jpeg_is_allowed(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        f = SimpleUploadedFile('photo.jpg', b'\xff\xd8\xff', content_type='image/jpeg')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_upload_docx_is_allowed(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        f = SimpleUploadedFile('report.docx', b'PK\x03\x04fake', content_type=mime)
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_disallowed_mime_type_returns_400(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        f = SimpleUploadedFile('script.sh', b'#!/bin/bash', content_type='text/x-shellscript')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = resp.json()
        assert 'File type not allowed' in str(body)

    def test_oversized_file_returns_400(self, api_client, employee_a, task_a, monkeypatch):
        from apps.crm.serializers import MAX_ATTACHMENT_SIZE
        api_client.force_authenticate(employee_a)
        # Temporarily lower the limit so we can trigger it with a small in-memory file
        monkeypatch.setattr('apps.crm.serializers.MAX_ATTACHMENT_SIZE', 5)
        f = SimpleUploadedFile('big.pdf', b'ABCDEF', content_type='application/pdf')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = resp.json()
        assert 'File size exceeds 50MB limit' in str(body)

    def test_no_file_no_storage_file_id_returns_400(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        resp = api_client.post(attachments_url(task_a.pk), data={}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated_gets_401(self, api_client, task_a):
        f = SimpleUploadedFile('doc.pdf', b'%PDF content', content_type='application/pdf')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_gets_403(self, api_client, guest_user, task_a):
        api_client.force_authenticate(guest_user)
        f = SimpleUploadedFile('doc.pdf', b'%PDF content', content_type='application/pdf')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_other_company_employee_gets_403(self, api_client, employee_b, task_a):
        api_client.force_authenticate(employee_b)
        f = SimpleUploadedFile('doc.pdf', b'%PDF content', content_type='application/pdf')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_uploaded_by_is_set_to_request_user(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        f = SimpleUploadedFile('doc.pdf', b'%PDF content', content_type='application/pdf')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_201_CREATED
        att = TaskAttachment.objects.get(task=task_a)
        assert att.uploaded_by == employee_a
        assert att.storage_file is None


# ---------------------------------------------------------------------------
# AC 1 Mode B: POST linking from Storage
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAttachmentLinkFromStorage:
    def test_employee_can_link_storage_file(self, api_client, employee_a, task_a, storage_file_a):
        api_client.force_authenticate(employee_a)
        resp = api_client.post(
            attachments_url(task_a.pk),
            data={'storage_file_id': storage_file_a.pk},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.json()
        assert data['filename'] == storage_file_a.name
        assert data['size'] == storage_file_a.file_size
        assert data['mime_type'] == storage_file_a.content_type

        att = TaskAttachment.objects.get(task=task_a)
        assert att.storage_file == storage_file_a
        assert not att.file

    def test_storage_file_from_other_company_returns_404(self, api_client, employee_a, task_a, company_b, admin_a):
        api_client.force_authenticate(employee_a)
        other_folder = Folder.objects.create(name='Other', owner=admin_a, company=company_b)
        other_file = StorageFile.objects.create(
            name='other.pdf',
            file=SimpleUploadedFile('other.pdf', b'content', content_type='application/pdf'),
            file_size=7,
            content_type='application/pdf',
            folder=other_folder,
            owner=admin_a,
            company=company_b,
        )
        resp = api_client.post(
            attachments_url(task_a.pk),
            data={'storage_file_id': other_file.pk},
            format='json',
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_nonexistent_storage_file_returns_404(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        resp = api_client.post(
            attachments_url(task_a.pk),
            data={'storage_file_id': 99999},
            format='json',
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_superadmin_can_link_any_storage_file(self, api_client, superadmin, task_a, storage_file_a):
        api_client.force_authenticate(superadmin)
        resp = api_client.post(
            attachments_url(task_a.pk),
            data={'storage_file_id': storage_file_a.pk},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# AC 3: DELETE /api/v1/crm/tasks/<task_pk>/attachments/<pk>/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAttachmentDelete:
    def test_uploader_can_delete_their_attachment(self, api_client, employee_a, task_a, attachment_a):
        attachment_a.uploaded_by = employee_a
        attachment_a.save()
        api_client.force_authenticate(employee_a)
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment_a.pk))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not TaskAttachment.objects.filter(pk=attachment_a.pk).exists()

    def test_company_admin_can_delete_any_attachment(self, api_client, admin_a, task_a, attachment_a):
        # attachment was uploaded by admin_a (fixture default), but test deletes as admin
        api_client.force_authenticate(admin_a)
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment_a.pk))
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_other_employee_cannot_delete(self, api_client, employee_a, task_a, attachment_a, company_a):
        # attachment uploaded by admin_a; employee_a should not be able to delete
        assert attachment_a.uploaded_by != employee_a
        api_client.force_authenticate(employee_a)
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_from_other_company_gets_403(self, api_client, employee_b, task_a, attachment_a):
        api_client.force_authenticate(employee_b)
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment_a.pk))
        # employee_b cannot even see the task (403 from _get_task_or_403)
        assert resp.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_gets_401(self, api_client, task_a, attachment_a):
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment_a.pk))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_gets_403(self, api_client, guest_user, task_a, attachment_a):
        api_client.force_authenticate(guest_user)
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_storage_linked_attachment_does_not_delete_storage_file(
        self, api_client, admin_a, task_a, storage_file_a,
    ):
        attachment = TaskAttachment.objects.create(
            task=task_a,
            storage_file=storage_file_a,
            filename=storage_file_a.name,
            file_size=storage_file_a.file_size,
            mime_type=storage_file_a.content_type,
            uploaded_by=admin_a,
        )
        api_client.force_authenticate(admin_a)
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment.pk))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        # Attachment record removed
        assert not TaskAttachment.objects.filter(pk=attachment.pk).exists()
        # Storage file still exists
        assert StorageFile.objects.filter(pk=storage_file_a.pk).exists()

    def test_nonexistent_attachment_returns_404(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        resp = api_client.delete(attachment_detail_url(task_a.pk, 99999))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_superadmin_can_delete_any_attachment(self, api_client, superadmin, task_a, attachment_a):
        api_client.force_authenticate(superadmin)
        resp = api_client.delete(attachment_detail_url(task_a.pk, attachment_a.pk))
        assert resp.status_code == status.HTTP_204_NO_CONTENT


# ---------------------------------------------------------------------------
# AC 4: attachments_count in task detail
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAttachmentsCount:
    def test_attachments_count_is_zero_initially(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        resp = api_client.get(f'/api/v1/crm/tasks/{task_a.pk}/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['attachments_count'] == 0

    def test_attachments_count_reflects_actual_count(self, api_client, admin_a, task_a, attachment_a):
        api_client.force_authenticate(admin_a)
        resp = api_client.get(f'/api/v1/crm/tasks/{task_a.pk}/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['attachments_count'] == 1


# ---------------------------------------------------------------------------
# Storage quota tracking for task attachments
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAttachmentStorageQuota:
    """Verify that direct-upload attachments count against the company quota."""

    def test_direct_upload_blocked_when_quota_exceeded(self, api_client, company_a, admin_a, task_a):
        """Uploading a file that would push the company over its storage limit returns 400."""
        # Set storage limit to 1 byte so any upload exceeds it.
        company_a.storage_limit_gb = 0  # effectively 0 bytes limit
        company_a.save(update_fields=['storage_limit_gb'])

        api_client.force_authenticate(admin_a)
        f = SimpleUploadedFile('doc.pdf', b'%PDF-1.4 content', content_type='application/pdf')
        resp = api_client.post(attachments_url(task_a.pk), data={'file': f}, format='multipart')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = resp.json()
        assert 'Storage limit reached' in str(body)

    def test_direct_upload_counts_in_limits_helper(
        self, company_a, admin_a, task_a,
    ):
        """After a direct-upload attachment, get_company_storage_used_bytes reflects the file."""
        from apps.companies.limits import get_company_storage_used_bytes

        before = get_company_storage_used_bytes(company_a)

        content = b'%PDF-1.4 content for quota test'
        TaskAttachment.objects.create(
            task=task_a,
            file=SimpleUploadedFile('quota_test.pdf', content, content_type='application/pdf'),
            filename='quota_test.pdf',
            file_size=len(content),
            mime_type='application/pdf',
            uploaded_by=admin_a,
            storage_file=None,
        )

        after = get_company_storage_used_bytes(company_a)
        assert after == before + len(content)

    def test_storage_file_link_does_not_double_count(
        self, api_client, company_a, admin_a, task_a, storage_file_a,
    ):
        """Mode B (storage_file_id) must not double-count the file in the limits endpoint."""
        api_client.force_authenticate(admin_a)
        limits_url = f'/api/v1/companies/{company_a.pk}/limits/'

        # Record usage before linking the storage file as a task attachment.
        before = api_client.get(limits_url).json()['storage']['used_gb']

        # Link the existing storage file to the task (Mode B).
        resp = api_client.post(
            attachments_url(task_a.pk),
            data={'storage_file_id': storage_file_a.pk},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED

        # Storage usage must NOT increase because the file is already counted.
        after = api_client.get(limits_url).json()['storage']['used_gb']
        assert after == before

    def test_get_company_storage_used_bytes_includes_crm_attachments(
        self, company_a, task_a, admin_a,
    ):
        """Unit-level check: helper returns correct sum including CRM direct uploads."""
        from apps.companies.limits import get_company_storage_used_bytes

        before = get_company_storage_used_bytes(company_a)

        file_content = b'direct upload bytes'
        attachment = TaskAttachment.objects.create(
            task=task_a,
            file=SimpleUploadedFile('test.pdf', file_content, content_type='application/pdf'),
            filename='test.pdf',
            file_size=len(file_content),
            mime_type='application/pdf',
            uploaded_by=admin_a,
            storage_file=None,
        )

        after = get_company_storage_used_bytes(company_a)
        assert after == before + attachment.file_size

    def test_get_company_storage_used_bytes_excludes_storage_linked_attachments(
        self, company_a, task_a, admin_a, storage_file_a,
    ):
        """Mode B attachments must NOT be added on top of the storage file's own bytes."""
        from apps.companies.limits import get_company_storage_used_bytes

        before = get_company_storage_used_bytes(company_a)

        # Create a Mode B attachment (storage_file set, file field empty).
        TaskAttachment.objects.create(
            task=task_a,
            storage_file=storage_file_a,
            filename=storage_file_a.name,
            file_size=storage_file_a.file_size,
            mime_type=storage_file_a.content_type,
            uploaded_by=admin_a,
        )

        after = get_company_storage_used_bytes(company_a)
        # Usage must stay the same; the storage file was already counted before.
        assert after == before
