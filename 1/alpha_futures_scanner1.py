#!/usr/bin/env python3
"""
AlphaFutures Scanner - Профессиональный бот для сканирования фьючерсов на Bybit
Автоматически находит торговые сигналы на основе комплексного чеклиста
"""

import asyncio
import logging
import logging.handlers
import json
import os
import time
import signal
import sys
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv

import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from telegram import Bot
from telegram.error import TelegramError
import ast

from advanced_checklist_integration import add_advanced_checklist_to_bot

# Загружаем переменные из .env файла
load_dotenv()

# Настройка расширенного логирования
def setup_logging():
    """Настройка расширенного логирования с ротацией"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)  # Можно переключить на DEBUG для тестирования
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    
    file_handler = logging.handlers.RotatingFileHandler(
        'trading_bot.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

@dataclass
class BotConfig:
    """Конфигурация бота с валидацией"""
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    TESTNET: bool = False
    SCAN_INTERVAL: int = 300
    MAX_SYMBOLS: int = 50
    RISK_PER_TRADE: float = 1.0
    MIN_VOLUME_INCREASE: float = 1.2
    MAX_FUNDING_RATE: float = 0.0003
    MIN_LIQUIDITY: float = 20000
    RSI_MIN: int = 45
    RSI_MAX: int = 70
    ENABLE_HEALTH_CHECKS: bool = True
    HEALTH_CHECK_INTERVAL: int = 3600
    
    def __post_init__(self):
        """Валидация конфигурации после инициализации"""
        if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_CHAT_ID:
            raise ValueError("TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID обязательны")
        
        if self.RISK_PER_TRADE > 5:
            logger.warning("Риск на сделку превышает 5% - это может быть опасно")

class ConnectionManager:
    """Менеджер подключений с повторными попытками"""
    
    def __init__(self, max_retries: int = 5, backoff_factor: float = 2.0):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.retry_count = 0
    
    async def execute_with_retry(self, func, *args, **kwargs):
        """Выполнение функции с повторными попытками при ошибках"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                self.retry_count = 0
                return result
                
            except Exception as e:
                last_exception = e
                self.retry_count += 1
                wait_time = self.backoff_factor ** attempt
                
                logger.warning(
                    f"Попытка {attempt+1}/{self.max_retries} не удалась для {func.__name__}. "
                    f"Ошибка: {str(e)}"
                )
                
                if attempt < self.max_retries - 1:
                    logger.info(f"Повторная попытка через {wait_time:.1f} секунд...")
                    await asyncio.sleep(wait_time)
        
        logger.error(f"Все {self.max_retries} попыток не удались для {func.__name__}")
        raise last_exception

class SignalStrength(Enum):
    """Сила торгового сигнала"""
    WEAK = 0.3
    MEDIUM = 0.6
    STRONG = 0.8
    VERY_STRONG = 0.9

@add_advanced_checklist_to_bot
class AlphaFuturesScanner:
    """Основной класс бота AlphaFutures Scanner"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.connection_manager = ConnectionManager()
        self.telegram_bot = None
        self.bybit_client = None
        self.is_running = False
        self.last_scan_time = None
        self.start_time = datetime.now()
        self.errors_count = 0
        self.successful_scans = 0
        self.signals_sent = 0
        
        self.symbols_cache = {}
        self.kline_cache = {}
        self.cache_timeout = timedelta(minutes=5)
        
        self.setup_clients()
        self.load_trade_history()
        
        logger.info("AlphaFutures Scanner инициализирован")
    

    def setup_clients(self):
            """Инициализация клиентов с обработкой ошибок"""
            try:
                self.telegram_bot = Bot(token=self.config.TELEGRAM_BOT_TOKEN)
                if self.config.BYBIT_API_KEY and self.config.BYBIT_API_SECRET:
                    self.bybit_client = HTTP(
                        testnet=self.config.TESTNET,
                        api_key=self.config.BYBIT_API_KEY,
                        api_secret=self.config.BYBIT_API_SECRET
                    )
                else:
                    self.bybit_client = HTTP(testnet=self.config.TESTNET)
                
                logger.info("Клиенты успешно инициализированы")
                
            except Exception as e:
                logger.error(f"Ошибка инициализации клиентов: {e}\n{traceback.format_exc()}")
                raise
    
    def load_trade_history(self):
        try:
            if os.path.exists(self.trade_history_file) and os.path.getsize(self.trade_history_file) > 0:
                with open(self.trade_history_file, 'r', encoding='utf-8') as f:
                    self.trade_history = json.load(f)
            else:
                self.trade_history = []
            logger.info(f"Загружено {len(self.trade_history)} записей из истории")
        except Exception as e:
            logger.error(f"Ошибка загрузки истории торгов: {e}. Создаём новую историю.")
            self.trade_history = []
    
    def save_trade_history(self):
        """Сохранение истории торгов с обработкой ошибок и проверкой сериализации"""
        try:
            history_to_save = []
            for entry in self.trade_history[-1000:]:
                entry_copy = entry.copy()
                
                # Обработка signal
                if 'signal' in entry_copy and 'strength' in entry_copy['signal']:
                    strength = entry_copy['signal']['strength']
                    if isinstance(strength, SignalStrength):
                        entry_copy['signal']['strength'] = strength.name
                    elif isinstance(strength, str):
                        pass
                    else:
                        logger.warning(f"Неверный тип strength: {type(strength)}")
                
                # Рекурсивная конвертация numpy типов
                def convert_numpy(obj):
                    if isinstance(obj, np.bool_):
                        return bool(obj)
                    elif isinstance(obj, np.floating):
                        return float(obj)
                    elif isinstance(obj, np.integer):
                        return int(obj)
                    elif isinstance(obj, dict):
                        return {k: convert_numpy(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_numpy(v) for v in obj]
                    return obj
                
                entry_copy = convert_numpy(entry_copy)
                history_to_save.append(entry_copy)
            
            json_str = json.dumps(history_to_save, indent=2, ensure_ascii=False)
            with open(self.trade_history_file, 'w', encoding='utf-8') as f:
                f.write(json_str)
                f.flush()
                os.fsync(f.fileno())
            
            logger.info(f"Сохранено {len(history_to_save)} записей в trade_history.json")
            
        except Exception as e:
            logger.error(f"Ошибка сохранения истории торгов: {e}")
            raise

    @staticmethod
    def escape_markdown_v2(text: str) -> str:
        """Экранирование специальных символов для MarkdownV2"""
        special_chars = r'_ * [ ] ( ) ~ ` > # + - = | { } . !'
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text

    async def send_telegram_message(self, message: str):
            """Отправка сообщения в Telegram с использованием HTML"""
            try:
                # Конвертировать Markdown в HTML
                html_message = message.replace('*', '<b>').replace('*', '</b>')
                await self.connection_manager.execute_with_retry(
                    self.telegram_bot.send_message,
                    chat_id=self.config.TELEGRAM_CHAT_ID,
                    text=html_message,
                    parse_mode='HTML'
                )
                logger.info(f"Сообщение успешно отправлено в Telegram: {html_message[:50]}...")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения в Telegram: {str(e)}\n{traceback.format_exc()}")
                raise
    
    async def get_account_balance(self) -> Optional[float]:
        """Получение баланса аккаунта"""
        if not self.config.BYBIT_API_KEY:
            return None
            
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.bybit_client.get_wallet_balance(accountType="UNIFIED")
            )
            balance = float(response['result']['list'][0]['totalEquity'])
            logger.info(f"Текущий баланс: {balance:.2f} USDT")
            return balance
        except Exception as e:
            logger.error(f"Ошибка получения баланса: {e}")
            return None
    
    async def calculate_position_size(self, symbol: str, entry_price: float, stop_loss: float) -> Dict[str, float]:
        """Расчет размера позиции на основе риск-менеджмента"""
        try:
            balance = await self.get_account_balance()
            if not balance:
                logger.debug(f"Баланс недоступен для {symbol}, размер позиции = 0")
                return {'position_size': 0, 'risk_amount': 0, 'risk_percent': 0, 'leverage_suggestion': 1}
            
            risk_amount = balance * (self.config.RISK_PER_TRADE / 100)
            risk_per_unit = abs(entry_price - stop_loss)
            position_size = risk_amount / risk_per_unit if risk_per_unit != 0 else 0
            leverage_suggestion = min(20, max(1, int(balance / position_size))) if position_size != 0 else 1
            
            return {
                'position_size': position_size,
                'risk_amount': risk_amount,
                'risk_percent': self.config.RISK_PER_TRADE,
                'leverage_suggestion': leverage_suggestion
            }
        except Exception as e:
            logger.error(f"Ошибка расчета размера позиции для {symbol}: {e}")
            return {'position_size': 0, 'risk_amount': 0, 'risk_percent': 0, 'leverage_suggestion': 1}


    async def send_trade_history_summary(self):
        """Отправка сводки trade_history в Telegram"""
        try:
            if not self.trade_history:
                await self.send_telegram_message("⚠️ Нет торговых сигналов за это сканирование")
                logger.info("Нет сигналов для отправки сводки")
                return
            
            message = ["*📊 Сводка сигналов за сканирование:*\n"]
            passed_symbols = []
            for entry in self.trade_history[-15:]:  # Ограничим 15 записями, чтобы не превысить лимит
                signal = entry['signal']
                passed_symbols.append(signal['symbol'])
                message.append(f"- {signal['symbol']}: score {signal['score']:.2f}, {signal['direction']}")
            
            message.append(f"\n*Всего сигналов:* {len(self.trade_history)}")
            message.append("Подробности сохранены в trade_history.json")
            
            await self.send_telegram_message("\n".join(message))
            logger.info(f"Отправлена сводка по {len(passed_symbols)} сигналам")
        except Exception as e:
            logger.error(f"Ошибка отправки сводки: {str(e)}\n{traceback.format_exc()}")
            self.errors_count += 1


    async def send_signal(self, signal: Dict, checklist_results: Dict, position_info: Dict):
        """Отправка торгового сигнала в Telegram с укороченным сообщением и обработкой длины"""
        try:
            symbol = signal['symbol']
            strength = signal['strength'].name if isinstance(signal['strength'], SignalStrength) else signal['strength']
            
            message_parts = [
                f"📈 *Новый торговый сигнал для {symbol}*",
                "",
                f"*Сила сигнала:* {strength} ({signal['score']:.1%})",
                f"*Направление:* {signal['direction']}",
                "",
                "*🎯 Параметры входа:*",
                f"• Цена входа: ${signal['entry_price']:.4f}",
                f"• Стоп-лосс: ${signal['stop_loss']:.4f} (-{100*(1-signal['stop_loss']/signal['entry_price']):.1f}%)",
                f"• Тейк-профит: ${signal['take_profit']:.4f} (+{100*(signal['take_profit']/signal['entry_price']-1):.1f}%)",
                f"• Риск/прибыль: 1:{((signal['take_profit']-signal['entry_price'])/(signal['entry_price']-signal['stop_loss'])):.1f}",
                "",
                "*📊 Параметры позиции:*",
                f"• Размер позиции: {signal['position_size']:.2f} USDT",
                f"• Сумма риска: {signal['risk_amount']:.2f} USDT",
                f"• Риск на сделку: {position_info['risk_percent']:.1f}%",
                f"• Рекомендуемое плечо: {position_info['leverage_suggestion']}x",
                "",
                "*✅ Результаты чеклиста:*",
                f"• Базовый: {'✅' if checklist_results['basic_checklist']['passed'] else '❌'} (score: {checklist_results['basic_checklist']['score']:.2f})",
                f"• Расширенный: {'✅' if checklist_results['advanced_checklist']['passed'] else '❌'} (score: {checklist_results['advanced_checklist']['score']:.2f})",
                "",
                "*📈 Индикаторы:*",
                f"• RSI: {signal['indicators'].get('rsi', 'N/A'):.1f}" if 'rsi' in signal['indicators'] else "• RSI: N/A",
                f"• MACD: {signal['indicators'].get('macd', 'N/A'):.4f}" if 'macd' in signal['indicators'] else "• MACD: N/A",
                "",
                f"*⏰ Время:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                "⚠️ *ВНИМАНИЕ:* Всегда проверяйте параметры перед входом!",
                "Подробности в trade_history.json"
            ]
            
            message = "\n".join(message_parts)
            logger.info(f"Формируем сигнал для {symbol}: score = {signal['score']:.2f}, длина сообщения = {len(message)} символов")
            
            # Проверка длины и разбиение на части
            TELEGRAM_MAX_LENGTH = 4096
            if len(message) <= TELEGRAM_MAX_LENGTH:
                await self.send_telegram_message(message)
            else:
                # Разбиваем на части
                lines = message.split("\n")
                current_part = []
                current_length = 0
                for line in lines:
                    line_length = len(line) + 1  # +1 за \n
                    if current_length + line_length > TELEGRAM_MAX_LENGTH - 50:  # 50 символов запаса
                        await self.send_telegram_message("\n".join(current_part))
                        current_part = [f"*Продолжение сигнала для {symbol}*"]
                        current_length = len(current_part[0]) + 1
                    current_part.append(line)
                    current_length += line_length
                if current_part:
                    await self.send_telegram_message("\n".join(current_part))
            
            logger.info(f"Отправлен торговый сигнал для {symbol}")
            self.signals_sent += 1
            
        except Exception as e:
            logger.error(f"Ошибка отправки сигнала для {symbol}: {str(e)}\n{traceback.format_exc()}")
            self.errors_count += 1
    
    async def send_health_report(self):
        """Отправка отчета о состоянии бота"""
        try:
            uptime = datetime.now() - self.start_time
            hours, remainder = divmod(uptime.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            
            message = [
                "🤖 *ОТЧЕТ О СОСТОЯНИИ БОТА*",
                "",
                f"*Время работы:* {int(hours)}ч {int(minutes)}м {int(seconds)}с",
                f"*Последнее сканирование:* {self.last_scan_time.strftime('%H:%M:%S') if self.last_scan_time else 'Никогда'}",
                f"*Успешных сканирований:* {self.successful_scans}",
                f"*Отправлено сигналов:* {self.signals_sent}",
                f"*Ошибок:* {self.errors_count}",
                f"*Статус:* {'🟢 РАБОТАЕТ' if self.is_running else '🔴 ОСТАНОВЛЕН'}",
                "",
                f"*Следующее сканирование:* через {self.config.SCAN_INTERVAL} сек",
                f"*Мониторинг токенов:* {self.config.MAX_SYMBOLS}",
                f"*Риск на сделку:* {self.config.RISK_PER_TRADE}%",
                "",
                "_Отчет сгенерирован автоматически_"
            ]
            
            await self.send_telegram_message("".join(message))
            logger.info("Отправлен отчет о состоянии бота")
            
        except Exception as e:
            logger.error(f"Ошибка отправки отчета: {e}")
    
    async def get_all_futures_symbols(self) -> List[str]:
        """Получение списка всех фьючерсных пар"""
        try:
            response = self.bybit_client.get_instruments_info(category="linear")
            symbols = [item['symbol'] for item in response['result']['list']]
            logger.info(f"Получено {len(symbols)} фьючерсных пар")
            return symbols
        except Exception as e:
            logger.error(f"Ошибка получения списка символов: {e}")
            return []
    
    async def filter_symbols_by_liquidity(self, symbols: List[str]) -> List[str]:
        """Фильтрация символов по ликвидности"""
        filtered = []
        for symbol in symbols[:self.config.MAX_SYMBOLS]:
            try:
                ticker = self.bybit_client.get_tickers(category="linear", symbol=symbol)
                turnover = float(ticker['result']['list'][0]['turnover24h'])
                if turnover >= self.config.MIN_LIQUIDITY:
                    filtered.append(symbol)
                    logger.info(f"Символ {symbol} добавлен для анализа")
            except Exception as e:
                logger.warning(f"Ошибка проверки ликвидности для {symbol}: {e}")
        logger.info(f"После фильтрации осталось {len(filtered)} символов")
        return filtered
    
    async def analyze_symbol(self, symbol: str) -> None:
        """Анализ одного символа"""
        try:
            data = {'klines': await self.bybit_client.get_kline(category="linear", symbol=symbol, interval="15", limit=100),
                    'orderbook': await self.bybit_client.get_orderbook(category="linear", symbol=symbol, limit=50)}
            indicators = {'current_price': float(data['klines']['result']['list'][0][4]),
                         'atr': np.mean([abs(float(k[2]) - float(k[3])) for k in data['klines']['result']['list'][:14]])}
            passed, score, results = await self.run_comprehensive_checklist(symbol, data, indicators)
            
            if passed:
                signal = {
                    'symbol': symbol,
                    'entry_price': indicators['current_price'],
                    'stop_loss': indicators['current_price'] * 0.98,
                    'take_profit': indicators['current_price'] * 1.03,
                    'score': score,
                    'strength': 'MEDIUM', 
                    'direction': 'Long',
                    'position_size': 0,
                    'risk_amount': 0,
                    'timestamp': datetime.now().isoformat(),
                    'indicators': indicators
                }
                position_info = await self.calculate_position_size(symbol, signal['entry_price'], signal['stop_loss'])
                signal.update(position_info)
                await self.send_signal(signal, results, position_info)
                self.trade_history.append({'signal': signal, 'checklist_results': results, 'timestamp': datetime.now().isoformat()})
                self.save_trade_history()
            
            logger.info(f"Чеклист для {symbol}: score = {score:.2f}, пройдено = {passed}")
            
        except Exception as e:
            logger.error(f"Ошибка анализа символа {symbol}: {e}")
            self.errors_count += 1
    
    async def scan_market(self):
        """Сканирование рынка на наличие сигналов"""
        try:
            logger.info("Начинаем сканирование рынка...")
            start_time = time.time()
            
            all_symbols = await self.get_all_futures_symbols()
            filtered_symbols = await self.filter_symbols_by_liquidity(all_symbols)
            
            if not filtered_symbols:
                logger.warning("Нет подходящих символов для анализа")
                return
            
            logger.info(f"Анализируем {len(filtered_symbols)} символов...")
            
            tasks = []
            for symbol in filtered_symbols:
                task = asyncio.create_task(self.analyze_symbol(symbol))
                tasks.append(task)
                
                if len(tasks) >= 10 or symbol == filtered_symbols[-1]:  # Уменьшено до 10 задач
                    await asyncio.gather(*tasks)
                    tasks = []
                    if len(filtered_symbols) > 10 and symbol != filtered_symbols[-1]:
                        await asyncio.sleep(0.2)  # Увеличена задержка
            
            if self.trade_history:
                await self.send_trade_history_summary()

            self.last_scan_time = datetime.now()
            self.successful_scans += 1
            
            scan_duration = time.time() - start_time
            logger.info(f"Сканирование завершено за {scan_duration:.2f} секунд")
            
        except Exception as e:
            logger.error(f"Ошибка сканирования рынка: {e}")
            self.errors_count += 1

    async def send_trade_history_file(self):
        """Отправка файла trade_history.json в Telegram как документ"""
        try:
            if os.path.exists(self.trade_history_file) and os.path.getsize(self.trade_history_file) > 0:
                await self.telegram_bot.send_document(
                    chat_id=self.config.TELEGRAM_CHAT_ID,
                    document=open(self.trade_history_file, 'rb'),
                    caption="📊 Trade History: все сигналы и чеклисты за сессию"
                )
                logger.info("Отправлен файл trade_history.json в Telegram")
            else:
                await self.send_telegram_message("⚠️ Нет данных в trade_history.json")
                logger.info("Файл trade_history.json пуст или отсутствует")
        except Exception as e:
            logger.error(f"Ошибка отправки файла trade_history.json: {str(e)}\n{traceback.format_exc()}")
            await self.send_telegram_message(f"❌ Ошибка отправки trade_history.json: {str(e)}")
            self.errors_count += 1
    
    async def start(self):
            """Запуск бота"""
            try:
                self.setup_clients()
                self.load_trade_history()
                logger.info("AlphaFutures Scanner инициализирован")
                
                try:
                    await self.send_telegram_message("🚀 AlphaFutures Scanner запущен!")
                except Exception as e:
                    logger.error(f"Не удалось отправить сообщение о запуске: {str(e)}\n{traceback.format_exc()}")
                    # Продолжаем работу, несмотря на ошибку отправки
                
                logger.info("AlphaFutures Scanner запущен и начал работу")
                
                tasks = [
                    asyncio.create_task(self.scan_market()),
                    asyncio.create_task(self.health_check_loop())
                ]
                
                await asyncio.gather(*tasks)
                
            except KeyboardInterrupt:
                logger.info("Бот остановлен пользователем (Ctrl+C)")
            except Exception as e:
                logger.error(f"Ошибка работы бота: {e}\n{traceback.format_exc()}")
            finally:
                await self.send_telegram_message("🛑 AlphaFutures Scanner остановлен")
                await self.send_trade_history_file()
                logger.info("AlphaFutures Scanner завершил работу")

    
    async def health_check_loop(self):
        """Цикл проверки здоровья бота"""
        while self.is_running:
            try:
                await asyncio.sleep(self.config.HEALTH_CHECK_INTERVAL)
                
                if (self.last_scan_time and 
                    (datetime.now() - self.last_scan_time).total_seconds() > self.config.SCAN_INTERVAL * 2):
                    await self.send_telegram_message(
                        "⚠️ *ВНИМАНИЕ:* Бот не выполнял сканирование дольше ожидаемого времени"
                    )
                
                if self.config.HEALTH_CHECK_INTERVAL >= 21600:
                    await self.send_health_report()
                    
            except Exception as e:
                logger.error(f"Ошибка в health check loop: {e}")
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Получена команда остановки бота...")
        self.is_running = False

def setup_signal_handlers(bot: AlphaFuturesScanner):
    """Настройка обработчиков сигналов для graceful shutdown"""
    def signal_handler(signum, frame):
        logger.info(f"Получен сигнал {signum} в {datetime.now()}, стек вызовов: {''.join(traceback.format_stack(frame))}")
        asyncio.create_task(bot.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

async def main():
    """Основная функция запуска бота"""
    try:
        config = BotConfig(
            TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN', ''),
            TELEGRAM_CHAT_ID=os.getenv('TELEGRAM_CHAT_ID', ''),
            BYBIT_API_KEY=os.getenv('BYBIT_API_KEY', ''),
            BYBIT_API_SECRET=os.getenv('BYBIT_API_SECRET', ''),
            TESTNET=os.getenv('TESTNET', 'True').lower() == 'true',
            SCAN_INTERVAL=int(os.getenv('SCAN_INTERVAL', '300')),
            MAX_SYMBOLS=int(os.getenv('MAX_SYMBOLS', '50')),
            RISK_PER_TRADE=float(os.getenv('RISK_PER_TRADE', '1.0')),
            ENABLE_HEALTH_CHECKS=os.getenv('ENABLE_HEALTH_CHECKS', 'True').lower() == 'true',
            HEALTH_CHECK_INTERVAL=int(os.getenv('HEALTH_CHECK_INTERVAL', '3600'))
        )
        
        bot = AlphaFuturesScanner(config)
        setup_signal_handlers(bot)
        
        await bot.start()
        
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (Ctrl+C)")
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'):
        print("❌ Ошибка: Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
        print("\n📝 Инструкция по настройке:")
        print("1. Создайте файл .env в корне проекта\n")
        print("2. Заполните его по примеру .env.example\n")
        print("3. Получите TELEGRAM_BOT_TOKEN у @BotFather\n")
        print("4. Получите TELEGRAM_CHAT_ID у @userinfobot\n")
        print("\n⚡ Быстрый старт:")
        print("   pip install -r requirements.txt\n")
        print("   cp .env.example .env\n")
        print("   # отредактируйте .env файл\n")
        print("   python alpha_futures_scanner.py\n")
        sys.exit(1)
    
    # Удаление поврежденного файла отключено для сохранения истории
    # if os.path.exists('trade_history.json'):
    #     os.remove('trade_history.json')
    #     logger.info("\nУдалён повреждённый trade_history.json")
    
    asyncio.run(main())