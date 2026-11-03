# Scheduling RSVP deadline policy

Этот документ фиксирует service-level правила для публикации и переноса `Lesson`, связанные с `rsvp_deadline` и `decision_deadline`.

## Инварианты дедлайнов

Для `Lesson` действует порядок:

```text
rsvp_deadline <= decision_deadline <= starts_at
```

`rsvp_deadline` — последний момент, когда участник может отправить RSVP. Существующий `set_lesson_response()` допускает ответ при `now == rsvp_deadline` и отклоняет его только при `now > rsvp_deadline`.

## Late publish

`publish_lesson()` может выполнить переход:

```text
DRAFT -> RSVP_OPEN
```

только если:

```text
now <= rsvp_deadline
```

При `now > rsvp_deadline` сервис возвращает `ValidationError` и не должен создавать/активировать `LessonRosterEntry`, менять `status` или записывать `published_at`.

Граница `now == rsvp_deadline` намеренно разрешена и совпадает с семантикой `set_lesson_response()`: в сам момент дедлайна RSVP ещё допустим.

Правило применяется одинаково к Manager UI, scheduler/management command и другим callers, поскольку проверка находится в application service, а не во view.

## Near-term reschedule

`reschedule_lesson()` сохраняет относительные lead-time исходного занятия:

```text
rsvp_lead = source.starts_at - source.rsvp_deadline
decision_lead = source.starts_at - source.decision_deadline

replacement.rsvp_deadline = new_starts_at - rsvp_lead
replacement.decision_deadline = new_starts_at - decision_lead
```

Replacement создаётся как `DRAFT` и публикуется отдельным действием. Поэтому перенос допустим только если после вычисления:

```text
replacement.rsvp_deadline > now
```

При `replacement.rsvp_deadline <= now` перенос отклоняется до создания replacement и до отмены source Lesson. Равенство здесь намеренно отклоняется: в отличие от непосредственной публикации, replacement ещё должен пройти отдельный publish, поэтому нулевое оставшееся RSVP-окно практически создаёт «мёртвый» DRAFT.

Из инварианта `rsvp_deadline <= decision_deadline` следует, что будущий replacement RSVP deadline одновременно гарантирует, что `replacement.decision_deadline` также находится в будущем; отдельный guard для decision deadline не требуется.

RSVP-ответы исходного занятия при переносе по-прежнему не копируются. Активные enrollment/entitlement обрабатываются существующим canonical workflow `reschedule_lesson_with_entitlements()`.

## Manager UI

Manager UI показывает локализованную причину отказа:

- late publish: `Нельзя опубликовать занятие: срок ответа уже истёк.`
- near-term reschedule: `После переноса срок ответа должен оставаться в будущем. Выберите более позднее время начала занятия.`

Никакого manager-only обхода этих правил нет: для исключительного оперативного сценария требуется отдельный явно спроектированный workflow, а не обход application-service guard.
