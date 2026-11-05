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
- регистрацию, проверку, отклонение и отзыв медицинских оснований;
- сохранение места в группе;
- восстановление/перепривязку покрытия посещений;
- журнал доменных событий;
- приглашения и внешний вход через Яндекс/VK ID;
- обязательный TOTP MFA для привилегированных учетных записей и опциональный TOTP для обычного входа по паролю;
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

### Рекомендуемый путь — installation script

Запускайте команды из доверенного Git checkout проекта на production Linux-сервере. Нужны Bash, Git, Docker Engine и Docker Compose v2+, а также доступ текущего пользователя к Docker daemon. Docker API эквивалентен root-доступу — не доверяйте скрипт случайным пользователям.

~~~bash
./install.sh install       # при первом запуске создает .env.production и останавливается
# отредактируйте .env.production (секреты, APP_HOST, DNS, timezone), chmod 600
./install.sh install       # проверка env/Compose, build, migration gate, web health
./install.sh status        # revision, состояние контейнеров и health
./install.sh update        # backup, origin/main, build, migrations, rollout
./install.sh update --ref v1.2.3   # проверенный tag/commit/branch
./install.sh reinstall    # backup + rebuild + recreate, без удаления данных
./install.sh uninstall    # остановить/удалить контейнеры; тома/backup/env сохранить
~~~

Для принудительного уничтожения **именованных Docker volumes**, включая PostgreSQL и Caddy, существует только `./install.sh uninstall --purge-data`. Операция требует терминала и ввода `PURGE <APP_HOST>`. `.env.production` и backup-файлы в host bind mount даже в этом режиме не удаляются. **Не вызывайте** purge при обычной переустановке.

Важные инварианты: `update` требует чистый Git checkout и создаёт проверенный backup **до checkout/build/migrations**. Сборка и миграции выполняются на отдельном образе `candidate-...`; **только после успешного migration gate** он получает рабочий `APP_IMAGE_TAG` из `.env.production` (по умолчанию `local`), непосредственно перед переключением `web`. Неудачная сборка или миграция **не меняет действующий release-тег**: ручной `docker compose up` не запустит непроверенный candidate. После первого `update` Git checkout остаётся в состоянии **detached HEAD** — это ожидаемо, следующий `update` снова получает `origin/main`. Для несовместимых schema migrations нужен downtime и отдельный restore/forward-fix план; скрипт не выполняет автоматический rollback БД.

Ниже сохранён **ручной fallback** на случай отладки или недоступности скрипта. При ручном deploy всегда контролируйте build, exit code миграций и /healthz/.

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

### Первый запуск (ручной fallback)

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

## Обновление production (ручной fallback)

Критически важно: `git pull` сам по себе не обновляет код внутри уже запущенного Docker image.

Для ручного обновления **не собирайте новый образ прямо под действующим тегом**. Безопасная последовательность:

```bash
cd /opt/ice-school-schedule
git status --short # должно быть чисто
docker compose --env-file .env.production -f compose.production.yaml \
  run --rm --no-deps -T backup /ops/postgres_backup.sh --once
git fetch --prune --tags origin
git checkout --detach origin/main # либо проверенный tag/commit
git rev-parse HEAD

# Вручную задайте реальный APP_IMAGE_TAG из .env.production (по умолчанию local):
RELEASE_TAG=local
CANDIDATE_TAG="candidate-$(git rev-parse --short=12 HEAD)-manual-$$"
export APP_IMAGE_TAG="$CANDIDATE_TAG"
docker compose --env-file .env.production -f compose.production.yaml build migrate
docker compose --env-file .env.production -f compose.production.yaml \
  run --rm --no-deps -T migrate

# Только после успешного завершения всех предыдущих команд:
docker image tag "ice-school-app:$CANDIDATE_TAG" "ice-school-app:$RELEASE_TAG"
export APP_IMAGE_TAG="$RELEASE_TAG"
docker compose --env-file .env.production -f compose.production.yaml \
  up -d --no-deps --force-recreate web scheduler backup
docker compose --env-file .env.production -f compose.production.yaml \
  up -d --no-deps --force-recreate proxy
docker compose --env-file .env.production -f compose.production.yaml ps -a
```

При ручном выполнении используйте `set -e` либо проверяйте код возврата **каждого** шага: при ошибке backup, build или migrate нельзя выполнять дальнейшие команды. Для существующей БД первый install через ручную сборку активного тега также небезопасен — используйте `./install.sh install` или приведённую последовательность.

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

Локальные дампы **не зашифрованы**, создаются как `root:root 0600` и остаются полезными для быстрого восстановления. Сами по себе они **не защищают** от потери или компрометации VPS.

### Зашифрованные off-site backups — Issue #35

Опциональный `compose.offsite.yaml` запускает отдельный `offsite-backup` сервис с Restic. Он берёт только завершённые `ice_school_*.dump`, шифрует их **до отправки** на S3-compatible endpoint через HTTPS, применяет отдельный remote retention (14 daily / 8 weekly / 12 monthly) и публикует Docker healthcheck. Данные удалённого репозитория нельзя восстановить без отдельного пароля. Секреты берутся из двух файлов на сервере через Docker secrets и **не лежат в backup repository или Git**.

В `.env.production` активируйте `OFFSITE_ENABLED=1`, задайте `OFFSITE_RESTIC_REPOSITORY=s3:https://s3.example.net/school-bucket/restic`, `OFFSITE_RESTIC_PASSWORD_HOST_FILE` и `OFFSITE_AWS_CREDENTIALS_HOST_FILE` (абсолютные пути к файлам `0600` вне Git checkout). Предварительно создайте отдельный bucket и Restic repository (см. [руководство системного администратора](docs/Эксплуатационная%20документация/01_Руководство_системного_администратора.md)).

После инициализации:

```bash
./install.sh update
./install.sh status
```

При `OFFSITE_ENABLED=1` скрипт **не запускает миграции**, если свежий локальный dump не был успешно выгружен в encrypted off-site repository. При `OFFSITE_ENABLED=0` совместимость со старой установкой сохраняется, но `status` выводит явное предупреждение — это **незакрытый операционный риск**, а не рабочее резервирование.

CI создаёт изолированный Restic repository, шифрует реальный PostgreSQL dump, проверяет `restic check --read-data`, восстанавливает архив **без доступа к первоначальным файлам** и сверяет его побайтно. Окончательная приёмка требует отдельного реального restore-упражнения из S3 на другом хосте, а также внешнего алерта на отсутствие `OFFSITE_SUCCESS_PING_URL` и unhealthy `offsite-backup`.


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
https://<school-host>/accounts/external/callback/yandex/
https://<school-host>/accounts/external/callback/vk/
```

Yandex identity использует pairwise `psuid`; fallback на глобальный `id` намеренно запрещен.

У одного внутреннего User может быть не более одной identity каждого provider.

## Локальный вход и опциональный MFA

Обычный пользователь с локальным паролем может войти без Яндекс/VK. При этом двухфакторная защита **опциональна**: в разделе «Защита входа» пользователь может включить TOTP, получить recovery-коды, заменить authenticator или отключить TOTP после повторной проверки пароля и одноразового кода.

Включённый TOTP обязателен **только для локального password-login**. Вход обычного пользователя через Яндекс/VK не получает дополнительный TOTP challenge. Для привилегированных учетных записей локальный пароль + MFA остаются обязательными независимо от способа входа в старую сессию.

Автоматической публичной регистрации локальных пользователей нет. Администратор создаёт обычный User с паролем через Django Admin (без staff/superuser/manager permissions), а менеджер назначает доступ ученика или роль тренера через существующий UI. OAuth-only аккаунты сохраняют unusable password; в этом изменении самообслуживаемая установка локального пароля не добавляется.

## Привилегированный MFA

TOTP MFA обязателен для:

- `is_staff=True`;
- `is_superuser=True`;
- пользователя с любым manager-operation permission напрямую или через Django Group.

Такие аккаунты используют **локальный пароль + TOTP**. Внешний Яндекс/VK login для них отключен.

При первом входе создаются 10 recovery-кодов, которые показываются один раз. Повторное использование TOTP в одном 30-секундном окне блокируется.

Абсолютный срок MFA-verified session по умолчанию — 12 часов.

Break-glass recovery выполняется через `django-otp addstatictoken`; отключать MFA middleware ради восстановления нельзя.

## Служебные команды

Recurring/system operations представлены Django management commands. Основные:

```text
generate_lessons
publish_daily_schedule
evaluate_lesson
complete_lesson
process_subscription_lifecycle
```

Также доступны permission-checked команды для выдачи/отмены абонементов, занятий, состава групп, отработок, проверки/отклонения/отзыва medical workflow и расписания. Регистрация нового медицинского основания отдельной CLI-командой в текущей версии не реализована.

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
