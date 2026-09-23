import os
import logging
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from app.config import settings

logger = logging.getLogger(__name__)

class S3StorageManager:
    def __init__(self):
        self.bucket = settings.S3_BUCKET_NAME
        self.s3_client = None
        self._init_s3()

    def _init_s3(self):
        try:
            from botocore.config import Config
            config = Config(
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 3, "mode": "standard"}
            )
            self.s3_client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
                config=config
            )

            # Ensure bucket exists
            try:
                self.s3_client.create_bucket(Bucket=self.bucket)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"S3/MinIO initialization note (local filesystem fallback enabled): {e}")

    def upload_bytes(self, file_bytes: bytes, s3_key: str) -> str:
        if self.s3_client:
            try:
                self.s3_client.put_object(Bucket=self.bucket, Key=s3_key, Body=file_bytes)
                logger.info(f"File uploaded to S3 bucket '{self.bucket}' at key '{s3_key}'.")
                return f"s3://{self.bucket}/{s3_key}"
            except Exception as e:
                logger.warning(f"S3 upload error, fallback to local storage: {e}")

        # Local storage fallback
        local_dir = os.path.join(os.getcwd(), "data_store")
        os.makedirs(local_dir, exist_ok=True)
        file_path = os.path.join(local_dir, os.path.basename(s3_key))
        with open(file_path, "wb") as f:
            f.write(file_bytes)
        return file_path

    def download_bytes(self, s3_key: str) -> bytes:
        if self.s3_client:
            try:
                obj = self.s3_client.get_object(Bucket=self.bucket, Key=s3_key)
                return obj["Body"].read()
            except Exception as e:
                logger.warning(f"S3 download error, attempting local storage fallback: {e}")

        # Local storage fallback
        local_dir = os.path.join(os.getcwd(), "data_store")
        file_path = os.path.join(local_dir, os.path.basename(s3_key))
        if os.path.exists(file_path):
            with open(file_path, "rb") as f:
                return f.read()
        raise FileNotFoundError(f"File at key '{s3_key}' could not be retrieved from S3 or local storage.")

    def delete_file(self, s3_key: str) -> bool:
        success = False
        if self.s3_client:
            try:
                self.s3_client.delete_object(Bucket=self.bucket, Key=s3_key)
                success = True
            except Exception as e:
                logger.warning(f"Failed to delete file '{s3_key}' from S3: {e}")

        local_dir = os.path.join(os.getcwd(), "data_store")
        file_path = os.path.join(local_dir, os.path.basename(s3_key))
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                success = True
            except Exception as e:
                logger.warning(f"Failed to delete local fallback file '{file_path}': {e}")
        return success

storage_manager = S3StorageManager()

