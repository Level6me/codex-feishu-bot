"""全局运行时状态（阶段 5 重构：自 main.py 抽出）。"""

main_loop = None
running_processes = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}

_SEEN_MESSAGE_IDS = {}
_SEEN_MESSAGE_IDS_MAX = 1000


def _mark_seen(message_id, chat_id=None, create_time=None):
    """双层去重：内存快速层 + DB 持久层（跨重启防重放）。

    返回 True 表示该消息首次出现；False 表示已处理过。
    DB 写入失败时放行（避免丢消息）；DB 判断为旧消息时也缓存到内存，
    避免重复查库。
    """
    if message_id in _SEEN_MESSAGE_IDS:
        return False
    from database import mark_message_seen
    if not mark_message_seen(message_id, chat_id, create_time):
        _SEEN_MESSAGE_IDS[message_id] = True
        return False
    if len(_SEEN_MESSAGE_IDS) >= _SEEN_MESSAGE_IDS_MAX:
        oldest = next(iter(_SEEN_MESSAGE_IDS))
        del _SEEN_MESSAGE_IDS[oldest]
    _SEEN_MESSAGE_IDS[message_id] = True
    return True
