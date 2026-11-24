"""Local configuration. Responses never include credentials or credential fragments."""
from urllib.parse import urlsplit
import httpx
from .core import AppError
from .llm import is_local_model_url, request_provider, valid_model_url
from .secret_storage import SecretStorageError, save_env


def model_status(settings):
    missing = [name for name, value in [('API Key',settings.api_key),('API 地址',settings.base_url),('模型名称',settings.model)] if not value]
    return dict(configured=not missing, base_url=settings.base_url, model=settings.model,
                key_configured=bool(settings.api_key), missing=missing)


def validate_address(address):
    address=address.strip().rstrip('/')
    if not valid_model_url(address):
        raise AppError('INVALID_MODEL_CONFIG', 'Use an HTTPS API base URL or a local Ollama loopback URL.', 422)
    return address


def discover_models(settings,body,client=None):
    address=validate_address(body.base_url)
    key=body.api_key.strip() if body.api_key else settings.api_key
    if not key or any(c in key for c in '\r\n'):
        raise AppError('INVALID_MODEL_CONFIG','请先填写 API Key。',422)
    try:
        response = request_provider(
            (client or httpx).get, address + '/models',
            headers={'Authorization': 'Bearer ' + key}, timeout=settings.timeout,
            **({'trust_env': False} if client is None and is_local_model_url(urlsplit(address)) else {}),
        )
        models=response.json()['data']
        result=[dict(id=m['id'],name=m.get('name',m['id'])) for m in models if isinstance(m,dict) and isinstance(m.get('id'),str)]
        if not result:raise ValueError('Empty model list')
        return {'models':result}
    except (ValueError,KeyError,TypeError):
        raise AppError('PROVIDER_MODELS','该服务未返回兼容模型列表，请按服务商文档填写准确的模型 ID。',502)


def save_model_settings(settings, body):
    address,model=validate_address(body.base_url),body.model.strip()
    if not model or any(c in model for c in '\r\n'):
        raise AppError('INVALID_MODEL_CONFIG','请填写准确的模型 ID。',422)
    if urlsplit(address).hostname=='api.deepseek.com' and model.lower()=='deepseek':
        raise AppError('INVALID_MODEL_CONFIG','Deepseek 是服务名称，不是模型 ID。请点击读取可用模型并选择。',422)
    key = body.api_key.strip() if body.api_key else settings.api_key
    if not key or any(c in key for c in '\r\n'):
        raise AppError('INVALID_MODEL_CONFIG', 'An API key is required; leave blank only to retain an existing key.', 422)
    updates = {'QWEN_BASE_URL': address, 'QWEN_MODEL': model}
    if body.api_key:
        updates['QWEN_API_KEY'] = key
    try:
        save_env(settings.env_path, updates)
    except SecretStorageError as exc:
        raise AppError('SECRET_STORAGE_UNAVAILABLE', str(exc), 422) from exc
    settings.base_url, settings.model, settings.api_key = address, model, key
    return model_status(settings)
