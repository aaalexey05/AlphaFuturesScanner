#!/usr/bin/env python3
"""
Скрипт для тестирования оптимизированного сканирования AlphaFutures Scanner
"""

import asyncio
import time
import os
from dotenv import load_dotenv
from alpha_futures_scanner1 import AlphaFuturesScanner, BotConfig

load_dotenv()

async def quick_test_optimization():
    """Быстрое тестирование оптимизаций"""
    try:
        config = BotConfig(
            TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN'),
            TELEGRAM_CHAT_ID=os.getenv('TELEGRAM_CHAT_ID'),
            MAX_SYMBOLS=20,
            SCAN_INTERVAL=60
        )
        
        bot = AlphaFuturesScanner(config)
        
        start_time = time.time()
        signals = await bot.optimized_scan_market(max_symbols=15)
        duration = time.time() - start_time
        
        print(f"✅ Оптимизированное сканирование завершено за {duration:.2f} сек")
        print(f"📈 Найдено сигналов: {len(signals)}")
        
        return signals
    
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        return []

if __name__ == "__main__":
    asyncio.run(quick_test_optimization())