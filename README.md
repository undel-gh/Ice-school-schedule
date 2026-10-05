# Ice School Schedule

Web-система расписания, RSVP, посещаемости и абонементов для школы фигурного катания.

Стек: **Django 5.2 + PostgreSQL + server-rendered HTML + Docker Compose + Gunicorn + Caddy**.

Проект рассчитан на web-first работу: менеджеры, тренеры, ученики и родители выполняют обычные операции через web-интерфейс. Django Admin и management commands остаются вторичными средствами для системного администрирования, диагностики и аварийных операций.

## Возможности

Система поддерживает:

- учеников и доступ родителей/самих учеников;
- тренеров и группы;
- справочники типов занятий и площадок;
- регулярные шаблоны расписания с версионированием;
- автоматическую генерацию будущих занятий;
- публикацию, подтверждение, отмену, перенос и замену тренера;
- RSVP «Буду / Не буду»;
- мобильный интерфейс тренера и фиксацию посещаемости;
- абонементы с отдельными лимитами `ICE`/`HALL`;
- календарные, rolling 28-day и фиксированные 28-дневные расчетные периоды;
- разовые права и отработки;
- политики компенсаций отсутствий;
- медицинские основания;
- сохранение места в группе;
- восстановление/перепривязку покрытия посещений;
- журнал доменных событий;
- приглашения и внешний вход через Яндекс/VK ID;
- обязательный TOTP MFA для привилегированных учетных записей;
- production Docker Compose stack с TLS, scheduler и резервным копированием PostgreSQL.

## Граница финансовой ответственности

Приложение **не является бухгалтерской или платежной системой**.

Оно хранит операционные правила школы: расписание, посещения, лимиты абонементов, права на отработки/компенсации и отдельные факты подтверждения предусмотренной правилами оплаты.

Вне приложения остаются:

- расчет денежных сумм;
- персональные скидки;
- задолженность;
- зачисления и возвраты денег;
- окончательная сумма к оплате;
- интеграция с платежным провайдером.

`BILLING_RECALCULATION` остается зарезервированным историческим enum и не является рабочим действием текущего manager UI.

## Эксплуатационная документация

Основные руководства находятся в каталоге [`docs/Эксплуатационная документация`](docs/Эксплуатационная%20документация/README.md):

1. [Руководство системного администратора](docs/Эксплуатационная%20документация/01_Руководство_системного_администратора.md)
2. [Руководство менеджера](docs/Эксплуатационная%20документация/02_Руководство_менеджера.md)
3. [Руководство тренера](docs/Эксплуатационная%20документация/03_Руководство_тренера.md)
4. [Руководство пользователя — ученики и родители](docs/Эксплуатационная%20документация/04_Руководство_пользователя.md)

Архитектурная и предметная документация также находится в `docs/`:

- спецификация системы расписания, посещаемости и абонементов;
- Domain Model;
- State Machines и Domain Events;
- Django Models and Application Services Specification;
- документация по расчетным периодам, заморозке и web-операциям.

## Локальная разработка

### Python environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

set -a
source .env.example
set +a

python manage.py migrate
python manage.py check
python manage.py makemigrations --check
pytest -q
```

Если `POSTGRES_DB` не задан, Django использует SQLite. Это удобно для простых локальных проверок, но PostgreSQL является целевой БД и необходим для тестов блокировок/конкурентности.

### Локальный PostgreSQL

```bash
docker compose up -d db

set -a
source .env.example
set +a

python manage.py migrate
python manage.py check
pytest -q
```

Остановка:

```bash
docker compose down
```

`docker compose down -v` удаляет development volume БД и должен использоваться только намеренно.

## Time zone

`SCHOOL_TIME_ZONE` задает локальную IANA timezone школы, например:

```text
Europe/Riga
America/Toronto
Asia/Tokyo
```

`DJANGO_TIME_ZONE` может быть настроен отдельно. Формы/подписи школьного расписания используют `SCHOOL_TIME_ZONE`.

Тесты запускаются в нескольких time zone, чтобы бизнес-логика не зависела от timezone машины CI.

## Production

В репозитории есть односерверный pilot stack `compose.production.yaml`:

```text
Internet
   |
   v
Caddy :80/:443
   |
   v
Gunicorn / Django
   |
   v
PostgreSQL

scheduler -> lifecycle + generate_lessons
backup    -> verified PostgreSQL custom-format dumps
```

PostgreSQL не публикуется на host network. Gunicorn доступен только внутри Compose network. Caddy является единственной публичной точкой входа.

### Минимальная подготовка

```bash
cp .env.production.example .env.production
chmod 600 .env.production

mkdir -p backups
chmod 700 backups
```

Обязательно задайте реальные значения:

- `APP_HOST`;
- `DJANGO_ALLOWED_HOSTS`;
- `DJANGO_SECRET_KEY`;
- `POSTGRES_PASSWORD`;
- `SCHOOL_TIME_ZONE`;
- OAuth credentials, если используется внешний вход.

До запуска Caddy публичный DNS должен указывать на сервер, а TCP 80/443 — быть доступен для получения/обновления TLS-сертификатов.

### Первый запуск

```bash
docker compose --env-file .env.production -f compose.production.yaml config >/dev/null

docker compose --env-file .env.production -f compose.production.yaml build

docker compose --env-file .env.production -f compose.production.yaml up -d

docker compose --env-file .env.production -f compose.production.yaml ps -a
```

`migrate` является migration gate: `web` стартует только после успешного завершения миграций.

Проверка приложения:

```bash
curl -fsS https://<school-host>/healthz/ ; echo
```

Подробный процесс первого развертывания, обновления, backup/restore, monitoring, MFA recovery и incident handling находится в [руководстве системного администратора](docs/Эксплуатационная%20документация/01_Руководство_системного_администратора.md).

## Обновление production

Критически важно: `git pull` сам по себе не обновляет код внутри уже запущенного Docker image.

Базовый безопасный процесс:

```bash
cd /opt/ice-school-schedule
git status --short
git pull --ff-only
git rev-parse HEAD

docker compose --env-file .env.production -f compose.production.yaml build migrate

docker compose --env-file .env.production -f compose.production.yaml \
  up -d --force-recreate migrate

docker compose --env-file .env.production -f compose.production.yaml \
  ps -a migrate
```

Продолжать следует только после `Exited (0)` у `migrate`:

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  up -d --no-deps --force-recreate web scheduler
```

Если изменялись proxy/backup/Compose-настройки, выполните `up -d` для всего stack.

## Scheduler

Scheduler выполняет два независимых периодических процесса:

```text
process_subscription_lifecycle
    по умолчанию каждый час

generate_lessons --all-active --horizon-days 60
    по умолчанию каждые 6 часов
```

Scheduler запускается в **одном экземпляре**.

Текущая production-конфигурация намеренно не автоматизирует все школьные решения. В частности, публикация занятия и последующие решения по его состоянию остаются web-операциями менеджера/тренера.

Конфликт регулярной генерации с занятием другого типа фиксируется как `LessonGenerationConflict` и должен быть разрешен менеджером через web-интерфейс, а не прямым редактированием БД.

## Резервное копирование

`backup` создает custom-format PostgreSQL dump:

1. пишет `.partial`;
2. проверяет его через `pg_restore --list`;
3. атомарно переименовывает валидный архив в `.dump`;
4. применяет retention;
5. обновляет marker последнего успешного backup.

Дополнительный backup:

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  exec backup /ops/postgres_backup.sh --once
```

Локальный каталог `backups/` не является полной backup-стратегией. Проверенные дампы должны копироваться в защищенное off-host хранилище; восстановление необходимо периодически тестировать.

## Мониторинг

Docker healthcheck — локальный сигнал, а не система оповещения.

Для production рекомендуется:

- внешний uptime-check `https://<school-host>/healthz/`;
- dead-man monitoring lifecycle job;
- dead-man monitoring generation job;
- dead-man monitoring PostgreSQL backup.

Опциональные success ping URL задаются в `.env.production` и должны считаться секретами.

## Внешняя аутентификация

Onboarding ученика, родителя и обычного тренера выполняется только по приглашению. Неизвестная внешняя учетная запись не может самостоятельно создать школьный аккаунт.

Callback URLs:

```text
https://<school-host>/accounts/external/yandex/callback/
https://<school-host>/accounts/external/vk/callback/
```

Yandex identity использует pairwise `psuid`; fallback на глобальный `id` намеренно запрещен.

У одного внутреннего User может быть не более одной identity каждого provider.

## Привилегированный MFA

TOTP MFA обязателен для:

- `is_staff=True`;
- `is_superuser=True`;
- пользователя с любым manager-operation permission напрямую или через Django Group.

Такие аккаунты используют **локальный пароль + TOTP**. Внешний Яндекс/VK login для них отключен.

При первом входе создаются 10 recovery-кодов, которые показываются один раз. Повторное использование TOTP в одном 30-секундном окне блокируется.

Абсолютный срок MFA-verified session по умолчанию — 12 часов.

Break-glass recovery выполняется через `django-otp addstatictoken`; отключать MFA middleware ради восстановления нельзя.

## Management commands

Recurring/system operations представлены Django management commands. Основные:

```text
generate_lessons
publish_daily_schedule
evaluate_lesson
complete_lesson
process_subscription_lifecycle
```

Также доступны permission-checked команды для выдачи/отмены абонементов, занятий, состава групп, отработок, medical workflow и расписания.

Посмотреть актуальный список:

```bash
python manage.py help
```

Обычная работа менеджеров и тренеров должна выполняться через web UI. CLI не должен становиться единственным интерфейсом для человеческого бизнес-процесса.

## Проверки перед merge/deploy

Минимум:

```bash
python manage.py check
python manage.py makemigrations --check
pytest -q
```

Production-like check:

```bash
python manage.py check --deploy
```

CI также выполняет production image/Compose/Caddy smoke checks, scheduler/backup smoke tests и restore smoke-test PostgreSQL dump.

## Безопасность

Ключевые принципы проекта:

- production `DEBUG` выключен;
- secret key обязателен;
- парольные попытки ограничиваются `django-axes`;
- доверяется только `X-Real-IP` от явно настроенного reverse proxy;
- `X-Forwarded-For` для lockout намеренно игнорируется;
- чувствительные invitation/OAuth/reset URL исключены из Caddy request logging;
- Gunicorn access log отключен, чтобы не дублировать sensitive request targets;
- Docker json-file logs ограничены ротацией;
- privileged accounts защищены MFA;
- обычные пользователи создаются только через контролируемое приглашение;
- business history не должна исправляться прямой правкой таблиц.

## Статус проекта

Текущая версия предназначена для пилотной эксплуатации. Перед реальным массовым использованием рекомендуется пройти полный сценарный pilot с тестовыми менеджерами, тренерами, учениками/родителями, проверить backup/restore и настроить внешний мониторинг.

## Лицензия

См. [`LICENSE`](LICENSE).