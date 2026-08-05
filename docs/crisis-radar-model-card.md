# Crisis Radar model card

## Назначение

Детерминированная система глобального раннего предупреждения. Не торговая стратегия, не автоматическое исполнение и не гарантия кризиса.

## Активные версии

- methodology: `candidate-v10`;
- trend features: `trend-regime-v1`;
- event extraction: version stored with every cluster;
- scenario fusion: `scenario-fusion-v1`;
- historical replay: `historical-replay-v1`.

## Выходы

- market stage;
- scenario strength 0–100;
- data reliability;
- historical probability только после promotion gate;
- conditional opportunity map.

## Запрещённые применения

- утверждать точную дату кризиса;
- открывать сделки автоматически;
- показывать вероятность, если gate не пройден;
- использовать LLM как числовой risk engine;
- трактовать один headline или vector match как факт.

## Проверка и статус

Движок causal replay и walk-forward реализован. Реальные 15/30-дневные Bybit replay ранее не победили baseline и дали recall 0; поэтому live probability остаётся `null`. Новый v10/fusion требует накопления/разметки ≥30 независимых событий и отдельных region/crisis holdout. До выполнения этих условий прогностическая сила считается экспериментальной.

## Известные ограничения

- бесплатные API имеют задержки, лимиты и неодинаковые vintages;
- годовые/квартальные ряды запаздывают;
- GDELT используется только как необязательный discovery feed;
- бесплатного подтверждённого TradFi-options feed нет;
- live canary должен отработать 14 календарных дней без ложного stable и дублей уведомлений.
