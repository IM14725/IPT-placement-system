from celery import Celery

from app.core.config import settings

celery_app = Celery("ipt_marketplace")
celery_app.conf.broker_url = settings.broker_url
celery_app.conf.result_backend = settings.result_url


def enqueue(name: str, *args, **kwargs):
    return celery_app.send_task(name, args=args, kwargs=kwargs)