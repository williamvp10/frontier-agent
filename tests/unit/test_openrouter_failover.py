import asyncio
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import Settings
from app.models.factory import build_visual_model
from app.models.openrouter_keys import OpenRouterKeys
from app.models.openrouter_visual import OpenRouterVisualModel, ProviderCallError
from app.models.schemas import Plan

PRIMARY = 'synthetic-primary-credit-key'
BACKUP = 'synthetic-backup-credit-key'
ANSWER = {'intent': 'query', 'milestones': ['Count'], 'success_criteria': ['Visible value'],
          'preservation_rules': ['No writes']}


def completion():
    return httpx.Response(200, json={'id': 'chat_test', 'object': 'chat.completion',
        'created': 1, 'model': 'openai/gpt-6.1-sol', 'choices': [{'index': 0,
        'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(ANSWER)}}],
        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})


def sdk(key, http):
    return ChatOpenAI(api_key=key, model='openai/gpt-6.1-sol',
        base_url='https://openrouter.ai/api/v1', use_responses_api=False, max_retries=0,
        http_async_client=http)


async def test_credit_failover_retries_same_inference_and_stays_on_backup_across_roles(caplog):
    calls = []

    def respond(request):
        calls.append(request)
        if request.headers['authorization'] == 'Bearer ' + PRIMARY:
            return httpx.Response(402, json={'error': {'message': PRIMARY + BACKUP,
                'metadata': {'limit_source': 'openrouter_key_limit'}}})
        return completion()

    keys = OpenRouterKeys(SecretStr(PRIMARY), SecretStr(BACKUP))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        models = [OpenRouterVisualModel(api_key=SecretStr(PRIMARY), keys=keys,
            client=sdk(PRIMARY, http), backup_client=sdk(BACKUP, http)) for _ in range(2)]
        result = await models[0].decide('operator', Plan, {'instruction': 'Count'}, b'PNG')
        await models[1].decide('verifier', Plan, {'instruction': 'Check'}, b'PNG')
        await models[0].decide('operator', Plan, {'instruction': 'Count'}, b'PNG')
    assert result.intent == 'query'
    assert [r.headers['authorization'] for r in calls] == [
        'Bearer ' + PRIMARY, 'Bearer ' + BACKUP, 'Bearer ' + BACKUP, 'Bearer ' + BACKUP]
    assert calls[0].content == calls[1].content
    assert all(m.last_metadata['credential_slot'] == 'backup' for m in models)
    assert PRIMARY not in caplog.text and BACKUP not in caplog.text
    assert PRIMARY not in repr(keys) and BACKUP not in repr(keys)


@pytest.mark.parametrize('status,metadata', [(401, {}), (429, {}), (503, {}), (400, {}),
    (402, {'limit_source': 'openrouter_in_flight_budget'}),
    (402, {'is_byok': True}), (402, {'provider_name': 'upstream'})])
async def test_unrelated_errors_do_not_switch_or_leak_provider_body(status, metadata):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={'error': {'message': PRIMARY + BACKUP,
            'metadata': metadata}})

    keys = OpenRouterKeys(SecretStr(PRIMARY), SecretStr(BACKUP))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = OpenRouterVisualModel(api_key=SecretStr(PRIMARY), keys=keys,
            client=sdk(PRIMARY, http), backup_client=sdk(BACKUP, http))
        with pytest.raises(ProviderCallError) as error:
            await model.decide('operator', Plan, {}, b'PNG')
    assert len(calls) == 1 and keys.slot == 'primary'
    assert error.value.status_code == status
    assert PRIMARY not in str(error.value) and BACKUP not in str(error.value)


@pytest.mark.parametrize('backup', ['', PRIMARY, BACKUP])
async def test_exhausted_backup_is_bounded_and_missing_or_duplicate_backup_is_not_retried(backup):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(402, json={'error': {'message': 'Insufficient credits'}})

    keys = OpenRouterKeys(SecretStr(PRIMARY), SecretStr(backup))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = OpenRouterVisualModel(api_key=SecretStr(PRIMARY), keys=keys,
            client=sdk(PRIMARY, http), backup_client=sdk(BACKUP, http))
        with pytest.raises(ProviderCallError) as error:
            await model.decide('operator', Plan, {}, b'PNG')
    assert len(calls) == (2 if backup == BACKUP else 1)
    assert error.value.category == 'credits_exhausted'


async def test_failover_preserves_one_deadline():
    async def respond(request):
        await asyncio.sleep(0.04)
        if request.headers['authorization'] == 'Bearer ' + PRIMARY:
            return httpx.Response(402, json={'error': {'message': 'Insufficient credits'}})
        return completion()

    keys = OpenRouterKeys(SecretStr(PRIMARY), SecretStr(BACKUP))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = OpenRouterVisualModel(api_key=SecretStr(PRIMARY), keys=keys, timeout=0.06,
            client=sdk(PRIMARY, http), backup_client=sdk(BACKUP, http))
        with pytest.raises(TimeoutError):
            await model.decide('operator', Plan, {}, b'PNG')
    assert keys.slot == 'backup'


def test_factory_shares_key_selection_and_settings_hide_backup():
    settings = Settings(openrouter_api_key=PRIMARY, openrouter_api_key_backup=BACKUP)
    router = build_visual_model(settings)
    assert all(m.keys is router.keys for m in router.models.values())
    assert BACKUP not in repr(settings)
