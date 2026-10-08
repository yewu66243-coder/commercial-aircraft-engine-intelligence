"""Bounded continuation of length-limited report completions."""
import asyncio


CONTINUE_PROMPT = (
    '上一次输出因长度上限中断。请从最后一个字符之后继续输出剩余内容，'
    '不要重新开始报告，不重复已有文字，不添加续写说明或代码围栏。'
    '保留原有引用编号，补完中断的句子或表格以及尚未完成的章节和来源列表。'
    '只使用前文提供的证据，不新增未经支持的事实。'
)


def _merge(prefix, suffix):
    # Exact overlaps only: do not guess at semantic rewrites of facts or citations.
    for size in range(min(len(prefix), len(suffix)), 19, -1):
        if prefix.endswith(suffix[:size]):
            return prefix + suffix[size:]
    return prefix + suffix


async def complete_writer(client, *, messages, model, max_tokens, options=None, log=None,
                          max_continuations=2, timeout_seconds=180):
    content, attempts = '', []
    status, warning = 'incomplete', ''
    history = list(messages)
    for index in range(max_continuations + 1):
        attempt = {'attempt': index + 1, 'kind': 'initial' if index == 0 else 'continuation',
                   'model': model, 'max_tokens': max_tokens}
        attempts.append(attempt)
        try:
            response = await asyncio.wait_for(client.chat.completions.create(
                model=model, temperature=0, max_tokens=max_tokens,
                messages=history, **(options or {})), timeout=timeout_seconds)
            usage = getattr(response, 'usage', None)
            attempt['usage'] = {key: getattr(usage, key, None) for key in
                                ('prompt_tokens', 'completion_tokens', 'total_tokens')}
            choice = response.choices[0]
            reason = getattr(choice, 'finish_reason', None)
            chunk = choice.message.content or ''
            attempt.update(finish_reason=reason, output_characters=len(chunk))
        except Exception as exc:
            attempt.update(error_type=type(exc).__name__, http_status=getattr(exc, 'status_code', None))
            status = 'request_failed'
            warning = ('续写请求失败，已保留此前正文为草稿。' if content else
                       '综合写作请求失败，已保存研究材料，当前文件为待整理草稿。')
            warning += f'（{type(exc).__name__}）'
            break
        if not chunk.strip():
            status, warning = 'empty_response', '模型返回空内容，未完成写作；已有内容已保留为草稿。'
            break
        if index and (chunk.strip() in content or chunk.lstrip().startswith('# ')):
            status, warning = 'invalid_continuation', '续写重复已有正文或重新开始报告，未拼接该段，已保留草稿。'
            break
        content = _merge(content, chunk) if index else chunk
        if log:
            log(f"写作第{index + 1}次响应：finish_reason={reason}，"
                f"输出tokens={attempt['usage']['completion_tokens']}，单次上限={max_tokens}。")
        if reason == 'stop':
            status = 'completed'
            break
        if reason != 'length':
            status, warning = 'interrupted', f'模型输出异常结束（finish_reason={reason}），已保存草稿。'
            break
        status, warning = 'truncated', '模型输出达到长度上限，续写次数已用尽，已保存现有内容为草稿。'
        if index < max_continuations:
            if log:
                log(f'模型输出达到长度上限，正在续写（{index + 1}/{max_continuations}）。')
            history = list(messages) + [{'role': 'assistant', 'content': content},
                                        {'role': 'user', 'content': CONTINUE_PROMPT}]
    return {'content': content, 'status': status, 'warning': '' if status == 'completed' else warning,
            'attempts': attempts, 'continuation_count': max(0, len(attempts) - 1)}
