"""全局运行时状态（阶段 5 重构：自 main.py 抽出）。"""

main_loop = None
running_processes = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}

_SEEN_MESSAGE_IDS = {}
_SEEN_MESSAGE_IDS_MAX = 1000


def _mark_seen(message_id):
    if message_id in _SEEN_MESSAGE_IDS:
        return False
    if len(_SEEN_MESSAGE_IDS) >= _SEEN_MESSAGE_IDS_MAX:
        oldest = next(iter(_SEEN_MESSAGE_IDS))
        del _SEEN_MESSAGE_IDS[oldest]
    _SEEN_MESSAGE_IDS[message_id] = True
    return True
