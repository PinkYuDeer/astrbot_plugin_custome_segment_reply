import asyncio
import random
import json
from typing import List  # ✅ 新增：兼容 Python 3.8 的类型提示

# ==================== 核心导入 (完全对齐你的可用环境) ====================
from astrbot.api.event import filter as event_filter
from astrbot.api.event import AstrMessageEvent
from astrbot.api.all import Context, Star, register, AstrBotConfig, logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.message_components import Plain

@register("astrbot_plugin_custome_segment_reply", "LinJohn8", "自定义规则本地智能分段", "1.0.0")
class CustomSegmentReplyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 确保 config 存在
        self.config = config or {}

        # 1. 强制分隔符配置
        raw_force = self.config.get("force_split_symbols")
        if not isinstance(raw_force, list) or len(raw_force) == 0:
            raw_force = []
        self.force_split_symbols = []
        for s in raw_force:
            if isinstance(s, str):
                cleaned_s = s.replace("\\n", "\n").strip("\r")
                if cleaned_s:
                    self.force_split_symbols.append(cleaned_s)
        
        # 2. 基础字数配置 (带类型安全转换)
        try:
            self.min_length = int(self.config.get("min_length", 20))
            self.max_length = int(self.config.get("max_length", 50))
        except (ValueError, TypeError):
            self.min_length = 20
            self.max_length = 50
            
        if self.min_length > self.max_length:
            self.min_length = self.max_length
            
        # 3. 超长处理配置
        self.allow_exceed_max = bool(self.config.get("allow_exceed_max", True))

        # 4. 绝对硬性截断配置
        try:
            self.hard_max_limit = int(self.config.get("hard_max_limit", 100))
        except (ValueError, TypeError):
            self.hard_max_limit = 100

        if self.hard_max_limit < self.max_length:
            self.hard_max_limit = self.max_length + 20

        # 5. 短尾合并配置
        self.merge_short_tail = bool(self.config.get("merge_short_tail", True))
        try:
            self.short_tail_threshold = int(self.config.get("short_tail_threshold", 8))
        except (ValueError, TypeError):
            self.short_tail_threshold = 8

        # 6. 符号与保留配置 (防呆清洗逻辑)
        raw_symbols = self.config.get("split_symbols")
        if not isinstance(raw_symbols, list) or len(raw_symbols) == 0:
            raw_symbols = [
                "\\n\\n", "\\n", "。", "！", "？", "；", "……", ".", "!", "?", ";", "”", "、", "，", ","
            ]
        
        self.split_symbols = []
        for s in raw_symbols:
            if isinstance(s, str):
                cleaned_s = s.replace("\\n", "\n").strip("\r")
                if cleaned_s:
                    self.split_symbols.append(cleaned_s)
                    
        if not self.split_symbols:
            self.split_symbols = ["\n\n", "\n", "。", "！", "？"]

        self.keep_symbol = bool(self.config.get("keep_symbol", True))

        # 7. 杂项配置
        exclude_kw = self.config.get("exclude_keywords", [])
        self.exclude_keywords = exclude_kw if isinstance(exclude_kw, list) else []
        
        delay_range = self.config.get("random_delay_range", [1, 3])
        if isinstance(delay_range, list) and len(delay_range) >= 2:
            try:
                self.delay_min = float(delay_range[0])
                self.delay_max = float(delay_range[1])
            except (ValueError, TypeError):
                self.delay_min = 1.0
                self.delay_max = 3.0
        else:
            self.delay_min = 1.0
            self.delay_max = 3.0

    @event_filter.on_decorating_result()
    async def handle_segment_reply(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return

        raw_text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):
                raw_text += comp.text.strip()
        raw_text = raw_text.strip()
        if not raw_text:
            return

        if self.exclude_keywords:
            text_lower = raw_text.lower()
            for keyword in self.exclude_keywords:
                if isinstance(keyword, str) and keyword and keyword.lower() in text_lower:
                    logger.info(f"检测到排除关键词 '{keyword}'，跳过自定义规则分段")
                    return

        try:
            logger.info(f"——准备进行自定义规则分段（原回复长度：{len(raw_text)}字符）——")
            
            segments = self.segment_text_by_rules(raw_text)
            
            if not segments or len(segments) <= 1:
                logger.info(f"——分段完成，无需拆分，保持 1 段输出——")
                return

            full_segmented_text = "\n\n".join(segments)
            
            result.chain.clear()
            
            for i, segment in enumerate(segments):
                if i > 0:
                    delay = random.uniform(self.delay_min, self.delay_max)
                    await asyncio.sleep(delay)
                await event.send(MessageChain().message(segment))
            
            await self._save_to_conversation_history(event, full_segmented_text)
            
            logger.info(f"——本地规则分段回复成功，共分 {len(segments)} 段——")
            
        except Exception as e:
            logger.error(f"本地规则分段异常，发送原消息。失败原因：{str(e)}")
            return

    @staticmethod
    def _skip_repeated_symbol(text: str, idx: int, symbol: str) -> int:
        """返回从 idx 开始连续重复 symbol 的总长度"""
        total_len = len(symbol)
        while text.startswith(symbol, idx + total_len):
            total_len += len(symbol)
        return total_len

    def _split_by_force_symbols(self, text: str) -> List[str]:
        """按强制分隔符预分段，连续重复的同一符号视为一个整体分隔符"""
        if not self.force_split_symbols:
            return [text]
        pieces = [text]
        for symbol in self.force_split_symbols:
            new_pieces = []
            for piece in pieces:
                i = 0
                last_end = 0
                while i <= len(piece) - len(symbol):
                    if piece.startswith(symbol, i):
                        rep_len = self._skip_repeated_symbol(piece, i, symbol)
                        if self.keep_symbol:
                            new_pieces.append(piece[last_end:i + rep_len])
                        else:
                            new_pieces.append(piece[last_end:i])
                        last_end = i + rep_len
                        i = last_end
                    else:
                        i += 1
                new_pieces.append(piece[last_end:])
            pieces = new_pieces
        return [p.strip() for p in pieces if p.strip()]

    # ✅ 修复：使用 List[str] 替代 list[str]，完美兼容 Python 3.8
    def segment_text_by_rules(self, text: str) -> List[str]:
        # 第一步：按强制分隔符预分段
        pre_segments = self._split_by_force_symbols(text.strip())

        # 第二步：对每个预分段分别应用长度规则分段
        segments = []
        for pre_seg in pre_segments:
            segments.extend(self._segment_by_length(pre_seg))

        # 短尾合并
        if self.merge_short_tail and len(segments) >= 2:
            last_seg = segments[-1]
            if len(last_seg) <= self.short_tail_threshold:
                tail = segments.pop()
                segments[-1] = segments[-1] + tail

        return segments

    def _segment_by_length(self, text: str) -> List[str]:
        segments = []
        remaining_text = text.strip()

        while remaining_text:
            if len(remaining_text) <= self.max_length:
                if remaining_text:
                    segments.append(remaining_text)
                break

            best_split_index = -1
            split_char_len = 0

            for symbol in self.split_symbols:
                idx = remaining_text.rfind(symbol, self.min_length, self.max_length)
                if idx != -1:
                    best_split_index = idx
                    split_char_len = self._skip_repeated_symbol(remaining_text, idx, symbol)
                    break

            if best_split_index == -1:
                if self.allow_exceed_max:
                    search_end = min(len(remaining_text), self.hard_max_limit)
                    found = False
                    for i in range(self.max_length, search_end):
                        for symbol in self.split_symbols:
                            if remaining_text.startswith(symbol, i):
                                best_split_index = i
                                split_char_len = self._skip_repeated_symbol(remaining_text, i, symbol)
                                found = True
                                break
                        if found:
                            break
                    
                    if best_split_index == -1:
                        best_split_index = search_end
                        split_char_len = 0
                else:
                    for symbol in self.split_symbols:
                        idx = remaining_text.rfind(symbol, 0, self.min_length)
                        if idx != -1:
                            best_split_index = idx
                            split_char_len = self._skip_repeated_symbol(remaining_text, idx, symbol)
                            break
                    
                    if best_split_index == -1:
                        best_split_index = self.max_length
                        split_char_len = 0

            if self.keep_symbol:
                cut_point = best_split_index + split_char_len
                seg = remaining_text[:cut_point].strip()
                if seg:
                    segments.append(seg)
                remaining_text = remaining_text[cut_point:].strip()
            else:
                seg = remaining_text[:best_split_index].strip()
                if seg:
                    segments.append(seg)
                remaining_text = remaining_text[best_split_index + split_char_len:].strip()

        return segments

    async def _save_to_conversation_history(self, event: AstrMessageEvent, content: str):
        try:
            conv_mgr = self.context.conversation_manager
            if not conv_mgr:
                return
            
            umo = event.unified_msg_origin
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            
            if curr_cid:
                conversation = await conv_mgr.get_conversation(umo, curr_cid)
                if conversation:
                    try:
                        history = json.loads(conversation.history) if isinstance(conversation.history, str) else conversation.history
                    except:
                        history = []
                    
                    user_content = event.message_str
                    if user_content:
                        if not history or history[-1].get("role") != "user":
                            history.append({
                                "role": "user",
                                "content": user_content
                            })
                    
                    history.append({
                        "role": "assistant",
                        "content": content
                    })
                    
                    await conv_mgr.update_conversation(
                        unified_msg_origin=umo,
                        conversation_id=curr_cid,
                        history=history
                    )
        except Exception as e:
            logger.error(f"保存对话历史失败: {str(e)}")

    async def terminate(self):
        logger.info("本地自定义规则分段插件已卸载，资源已释放")