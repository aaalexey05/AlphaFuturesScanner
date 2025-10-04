#!/usr/bin/env python3
"""
Интеграция расширенного чеклиста в основной бот
"""

import logging
from typing import Dict, Any, Tuple
from advanced_checklist import AdvancedChecklist, calculate_advanced_score

logger = logging.getLogger(__name__)

async def integrate_advanced_checklist(main_bot, symbol: str, data: Dict, indicators: Dict) -> Tuple[bool, float, Dict]:
    """Интеграция расширенного чеклиста в основной бот"""
    
    # Создаем экземпляр расширенного чеклиста
    advanced_checker = AdvancedChecklist(main_bot.bybit_client)
    
    # Результаты расширенных проверок
    advanced_results = {}
    
    try:
        # 🔍 Технический анализ (расширенный)
        advanced_results['multi_timeframe_alignment'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_multi_timeframe_alignment(symbol)
        advanced_results['multi_timeframe_alignment'] = {'passed': passed, 'details': details}
        
        # RSI дивергенции
        advanced_results['rsi_divergence'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_rsi_divergence(data['klines'])
        advanced_results['rsi_divergence'] = {'passed': passed, 'details': details}
        
        # Кластеры объема
        current_price = indicators.get('current_price', 0)
        advanced_results['volume_clusters'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_volume_clusters(data['orderbook'], current_price)
        advanced_results['volume_clusters'] = {'passed': passed, 'details': details}
        
        # 📈 Продвинутые индикаторы
        advanced_results['multi_timeframe_rsi'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_multi_timeframe_rsi(symbol)
        advanced_results['multi_timeframe_rsi'] = {'passed': passed, 'details': details}
        
        advanced_results['stochastic_momentum'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_stochastic_momentum(data['klines'])
        advanced_results['stochastic_momentum'] = {'passed': passed, 'details': details}
        
        # 🏛️ Фундаментальные факторы
        advanced_results['market_cap_volume'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_market_cap_volume(symbol)
        advanced_results['market_cap_volume'] = {'passed': passed, 'details': details}
        
        advanced_results['relative_volatility'] = {'passed': False, 'details': {}}
        current_atr = indicators.get('atr', 0)
        passed, details = await advanced_checker.check_relative_volatility(symbol, current_atr, current_price)
        advanced_results['relative_volatility'] = {'passed': passed, 'details': details}
        
        # ⏰ Временные факторы
        advanced_results['trading_session'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_trading_session()
        advanced_results['trading_session'] = {'passed': passed, 'details': details}
        
        advanced_results['seasonal_pattern'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_seasonal_pattern()
        advanced_results['seasonal_pattern'] = {'passed': passed, 'details': details}
        
        # 🎯 Психологические уровни
        advanced_results['psychological_levels'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_psychological_levels(current_price)
        advanced_results['psychological_levels'] = {'passed': passed, 'details': details}
        
        advanced_results['large_orders_clusters'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_large_orders_clusters(data['orderbook'], current_price)
        advanced_results['large_orders_clusters'] = {'passed': passed, 'details': details}
        
        # 🔄 Корреляционный анализ
        advanced_results['correlation_with_btc'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_correlation_with_btc(symbol, data['klines'])
        advanced_results['correlation_with_btc'] = {'passed': passed, 'details': details}
        
        # 🛡️ Управление рисками
        advanced_results['var_risk'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_var_risk(data['klines'])
        advanced_results['var_risk'] = {'passed': passed, 'details': details}
        
        advanced_results['max_drawdown'] = {'passed': False, 'details': {}}
        passed, details = await advanced_checker.check_max_drawdown(data['klines'])
        advanced_results['max_drawdown'] = {'passed': passed, 'details': details}
        
        # 📊 Расчет общего score
        advanced_score, detailed_scores = calculate_advanced_score(advanced_results)
        
        # Минимальный проходной балл - 60%
        passed = advanced_score >= 0.60
        
        logger.info(f"Расширенный чеклист для {symbol}: score = {advanced_score:.2f}, пройдено = {passed}")
        
        return passed, advanced_score, {
            'advanced_results': advanced_results,
            'detailed_scores': detailed_scores,
            'total_score': advanced_score
        }
        
    except Exception as e:
        logger.error(f"Ошибка выполнения расширенного чеклиста: {e}")
        return False, 0, {'error': str(e)}

# 🎯 Обновленный метод для основного класса бота

def add_advanced_checklist_to_bot(main_bot_class):
    """Добавление методов расширенного чеклиста в основной класс бота"""
    
    async def run_comprehensive_checklist(self, symbol: str, data: Dict, indicators: Dict) -> Tuple[bool, float, Dict]:
        """Комплексный чеклист (базовый + расширенный)"""
        
        # Запускаем базовый чеклист
        basic_passed, basic_score, basic_results = await self.run_enhanced_checklist(symbol, data, indicators)
        
        # Запускаем расширенный чеклист
        advanced_passed, advanced_score, advanced_results = await integrate_advanced_checklist(
            self, symbol, data, indicators
        )
        
        # Комбинированный score (70% базовый + 30% расширенный)
        combined_score = (basic_score * 0.7) + (advanced_score * 0.3)
        
        # Комбинированное решение
        combined_passed = combined_score >= 0.65
        
        logger.info(f"Комплексный чеклист для {symbol}: "
                   f"базовый = {basic_score:.2f}, расширенный = {advanced_score:.2f}, "
                   f"итоговый = {combined_score:.2f}, пройдено = {combined_passed}")
        
        return combined_passed, combined_score, {
            'basic_checklist': {
                'passed': basic_passed,
                'score': basic_score,
                'results': basic_results
            },
            'advanced_checklist': {
                'passed': advanced_passed,
                'score': advanced_score,
                'results': advanced_results
            },
            'combined_score': combined_score
        }
    
    # Добавляем метод в класс бота
    main_bot_class.run_comprehensive_checklist = run_comprehensive_checklist
    
    return main_bot_class