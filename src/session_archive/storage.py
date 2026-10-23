"""S3-совместимое хранилище записей сессий (SeaweedFS в docker-compose)."""

import logging
import os

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional

_log = logging.getLogger("archive.storage")


@dataclass
class S3Settings:
    endpoint:   Optional[str]
    access_key: str
    secret_key: str
    bucket:     str
    region:     str = "us-east-1"

    @classmethod
    def from_env(cls, prefix: str = "S3_") -> "S3Settings":
        return cls(
            endpoint=os.getenv(f"{prefix}ENDPOINT") or None,
            access_key=os.getenv(f"{prefix}ACCESS_KEY", "affectra"),
            secret_key=os.getenv(f"{prefix}SECRET_KEY", "affectra-secret"),
            bucket=os.getenv(f"{prefix}BUCKET", "affectra-recordings"),
            region=os.getenv(f"{prefix}REGION", "us-east-1"),
        )


def recording_keys(camera_id: str) -> Dict[str, str]:
    """
    Ключи объектов записи сессии.

    Args:
        camera_id (str): Идентификатор сессии.

    Returns:
        Dict[str, str]: Ключи video и analytics.
    """
    return {"video": f"sessions/{camera_id}/recording.mp4", "analytics": f"sessions/{camera_id}/analytics.json"}


class S3Storage:
    """Обёртка boto3 над одним бакетом; бакет создаётся при первом обращении."""

    def __init__(self, settings: S3Settings):
        import boto3

        from botocore.config import Config

        self.settings = settings
        self.bucket   = settings.bucket
        self._client  = boto3.client(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"max_attempts": 3}, connect_timeout=5, read_timeout=60),
        )
        self._bucket_ready = False

    def ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket)
            _log.info(f"Created bucket {self.bucket}")
        self._bucket_ready = True

    def upload_file(self, path: Path, key: str, content_type: str) -> None:
        self.ensure_bucket()
        self._client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
        _log.info(f"Uploaded s3://{self.bucket}/{key} ({path.stat().st_size / 1e6:.1f} MB)")

    def head(self, key: str) -> Optional[Dict[str, Any]]:
        from botocore.exceptions import ClientError

        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return None

    def get(self, key: str, byte_range: Optional[str] = None) -> Dict[str, Any]:
        """
        Объект или его диапазон байт.

        Args:
            byte_range (str, optional): Заголовок Range запроса, например "bytes=0-". Defaults to None.

        Returns:
            Dict[str, Any]: Ответ get_object: Body, ContentLength, ContentRange, ContentType.
        """
        kwargs = {"Bucket": self.bucket, "Key": key}
        if byte_range:
            kwargs["Range"] = byte_range
        return self._client.get_object(**kwargs)

    def delete_prefix(self, prefix: str) -> int:
        self.ensure_bucket()
        resp = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        keys = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
        if keys:
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
        return len(keys)
