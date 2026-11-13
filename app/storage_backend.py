"""Upload storage abstraction for local and containerized deploys."""

from __future__ import annotations

from pathlib import Path

from app.capture import storage as local_storage


class StorageBackend:
    def store_bytes(self, relative_path: str, data: bytes) -> str:
        raise NotImplementedError

    def read_bytes(self, relative_path: str) -> bytes:
        raise NotImplementedError

    def resolve_path(self, relative_path: str) -> Path:
        raise NotImplementedError

    def delete(self, relative_path: str | None) -> None:
        raise NotImplementedError


class LocalFilesystemStorage(StorageBackend):
    """Default backend used for local + single-node beta containers."""

    def store_bytes(self, relative_path: str, data: bytes) -> str:
        target = local_storage.upload_root() / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return relative_path

    def read_bytes(self, relative_path: str) -> bytes:
        return local_storage.read_stored_bytes(relative_path)

    def resolve_path(self, relative_path: str) -> Path:
        return local_storage.resolve_storage_path(relative_path)

    def delete(self, relative_path: str | None) -> None:
        local_storage.delete_stored_file(relative_path)


def get_storage_backend() -> StorageBackend:
    # Future object-storage backends can branch on LIFE_INBOX_STORAGE_BACKEND.
    return LocalFilesystemStorage()
