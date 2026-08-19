// Package bot связывает анкету «Точка входа» с MAX Bot API.
package bot

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"telegram-emulator/internal/maxbot/calendar"
	"telegram-emulator/internal/maxbot/config"
	"telegram-emulator/internal/maxbot/models"
	"telegram-emulator/internal/maxbot/policy"
	"telegram-emulator/internal/maxbot/storage"
	"telegram-emulator/internal/maxbot/survey"

	"github.com/max-messenger/max-bot-api-client-go/schemes"
	"go.uber.org/zap"
)

// Bot обрабатывает обновления MAX и ведёт анкету первичного обращения
type Bot struct {
	cfg      *config.Config
	outbox   Outbox
	store    *storage.Storage
	engine   *survey.Engine
	policies *policy.Set
	calendar *calendar.Calendar
	log      *zap.Logger
}

// New создаёт бота
func New(
	cfg *config.Config,
	outbox Outbox,
	store *storage.Storage,
	policies *policy.Set,
	workingCalendar *calendar.Calendar,
	log *zap.Logger,
) *Bot {
	return &Bot{
		cfg:      cfg,
		outbox:   outbox,
		store:    store,
		policies: policies,
		calendar: workingCalendar,
		log:      log,
		engine: survey.NewEngine(survey.Params{
			Districts:   cfg.Intake.Districts,
			Stop1:       policies.Stop1,
			ConsentText: cfg.Consent.Text,
		}),
	}
}

// HandleUpdate обрабатывает одно обновление MAX Bot API
func (b *Bot) HandleUpdate(ctx context.Context, update schemes.UpdateInterface) {
	switch u := update.(type) {
	case *schemes.BotStartedUpdate:
		b.handleStart(ctx, u.User)
		// Переход из паблика по ссылке вида ...?start=apply сразу открывает анкету
		if strings.EqualFold(strings.TrimSpace(u.Payload), "apply") {
			b.startIntake(ctx, u.User)
		}
	case *schemes.MessageCreatedUpdate:
		// Мессенджер может доставить сообщение повторно
		if !b.firstTime("msg:" + u.Message.Body.Mid) {
			return
		}
		b.handleMessage(ctx, u)
	case *schemes.MessageCallbackUpdate:
		// Повторное нажатие той же кнопки не должно повторять действие
		if !b.firstTime("cb:" + u.Callback.CallbackID) {
			b.answerCallback(ctx, u.Callback.CallbackID, "Этот ответ уже принят")

			return
		}
		b.handleCallback(ctx, u)
	default:
		b.log.Debug("обновление без обработчика", zap.String("type", string(update.GetUpdateType())))
	}
}

// firstTime сообщает, обрабатывается ли событие впервые.
//
// При ошибке хранилища событие обрабатывается: потерять обращение хуже,
// чем обработать его дважды.
func (b *Bot) firstTime(key string) bool {
	if strings.TrimSpace(key) == "" || strings.HasSuffix(key, ":") {
		return true
	}

	fresh, err := b.store.MarkProcessed(key)
	if err != nil {
		b.log.Warn("не удалось отметить событие обработанным", zap.Error(err))

		return true
	}

	return fresh
}

// handleStart показывает приветствие и главное меню
func (b *Bot) handleStart(ctx context.Context, user schemes.User) {
	b.send(ctx, user.UserId, greeting(b.cfg), b.menuKeyboard())
}

// handleMessage обрабатывает текстовое сообщение или присланный контакт
func (b *Bot) handleMessage(ctx context.Context, u *schemes.MessageCreatedUpdate) {
	user := u.Message.Sender
	if user.IsBot {
		return
	}

	text := strings.TrimSpace(u.Message.Body.Text)
	if strings.HasPrefix(text, "/") {
		b.handleCommand(ctx, user, text, u.Message.Recipient)

		return
	}

	// В групповом чате бот ведёт только служебные команды координаторов
	if u.Message.Recipient.ChatType != schemes.DIALOG && u.Message.Recipient.ChatId != 0 {
		return
	}

	phone := contactPhone(u.Message.Body.Attachments)
	if phone == "" && text == "" {
		return
	}

	b.processInput(ctx, user, survey.Input{Text: text, Phone: phone}, "")
}

// handleCommand обрабатывает команды бота
func (b *Bot) handleCommand(ctx context.Context, user schemes.User, text string, recipient schemes.Recipient) {
	command := strings.ToLower(strings.Fields(text)[0])
	argument := strings.TrimSpace(strings.TrimPrefix(text, strings.Fields(text)[0]))

	switch command {
	case "/start":
		b.handleStart(ctx, user)
	case "/apply", "/anketa":
		b.startIntake(ctx, user)
	case "/help":
		b.send(ctx, user.UserId, helpText(b.cfg), b.menuKeyboard())
	case "/my":
		b.showMyApplications(ctx, user)
	case "/cancel":
		b.cancelDraft(ctx, user)
	case "/id":
		b.showIdentifiers(ctx, user, recipient)
	case "/last":
		b.showRecent(ctx, user)
	case "/card":
		b.showCard(ctx, user, argument)
	case "/tasks":
		b.showTasks(ctx, user)
	case "/done":
		b.completeTask(ctx, user, argument)
	default:
		b.send(ctx, user.UserId, "Не знаю такой команды.\n\n"+helpText(b.cfg), b.menuKeyboard())
	}
}

// showIdentifiers сообщает идентификаторы для настройки получателей карточек
func (b *Bot) showIdentifiers(ctx context.Context, user schemes.User, recipient schemes.Recipient) {
	text := fmt.Sprintf("Ваш идентификатор в MAX: %d\nЕго указывают в coordinator.user_ids", user.UserId)

	if recipient.ChatType != schemes.DIALOG && recipient.ChatId != 0 {
		text = fmt.Sprintf("Идентификатор этого чата: %d\nЕго указывают в coordinator.chat_id\n\n"+
			"Ваш идентификатор в MAX: %d", recipient.ChatId, user.UserId)
		if err := b.outbox.Send(ctx, Message{ChatID: recipient.ChatId, Text: text}); err != nil {
			b.log.Error("не удалось отправить идентификаторы в чат", zap.Error(err))
		}

		return
	}

	b.send(ctx, user.UserId, text, nil)
}

// handleCallback обрабатывает нажатие кнопки
func (b *Bot) handleCallback(ctx context.Context, u *schemes.MessageCallbackUpdate) {
	user := u.Callback.User
	payload := u.Callback.Payload

	b.answerCallback(ctx, u.Callback.CallbackID, "")

	if strings.HasPrefix(payload, "m:") {
		switch strings.TrimPrefix(payload, "m:") {
		case "apply":
			b.startIntake(ctx, user)
		case "about":
			b.send(ctx, user.UserId, aboutText(b.cfg), b.menuKeyboard())
		case "emergency":
			b.send(ctx, user.UserId, emergencyText(), b.menuKeyboard())
		case "my":
			b.showMyApplications(ctx, user)
		}

		return
	}

	state, value, ok := parseAnswerPayload(payload)
	if !ok {
		return
	}

	b.processInput(ctx, user, survey.Input{Payload: value}, state)
}

// startIntake начинает или продолжает заполнение анкеты
func (b *Bot) startIntake(ctx context.Context, user schemes.User) {
	// До получения подписанного перечня STOP-1 линия не открывается
	if !b.policies.Stop1.Approved || len(b.policies.Stop1.Signs) == 0 {
		b.send(ctx, user.UserId, lineClosedText(b.cfg), nil)

		return
	}

	app, err := b.store.Draft(user.UserId)
	if err != nil {
		b.fail(ctx, user.UserId, "поиск черновика", err)

		return
	}

	if app != nil {
		b.send(ctx, user.UserId, "Продолжаем с того места, где остановились. Чтобы начать заново — /cancel.", nil)
		b.ask(ctx, user.UserId, app)

		return
	}

	previous, err := b.store.PreviousApplications(user.UserId, 5)
	if err != nil {
		b.log.Warn("не удалось проверить прошлые обращения", zap.Error(err))
	}

	app = &models.Application{
		Channel:           "max",
		UserID:            user.UserId,
		Username:          user.Username,
		Status:            models.StatusDraft,
		DataPolicyVersion: b.policies.DataCollection.Version,
		// Повторность по одному лишь идентификатору MAX не определяется:
		// с одного аккаунта могут обращаться о разных людях
		PossibleRepeat: len(previous) > 0,
	}
	if len(previous) > 0 {
		app.PreviousCase = previous[0].PublicID
	}
	app.State = b.engine.FirstState(app)

	if err := b.store.Create(app); err != nil {
		b.fail(ctx, user.UserId, "создание обращения", err)

		return
	}
	b.logEvent(app, "intake_started", "начато заполнение анкеты в MAX")

	b.send(ctx, user.UserId, "Прежде чем перейти к анкете, задам несколько обязательных вопросов о состоянии человека. "+
		"Отвечайте так, как видите ситуацию: мы ничего не оцениваем сами и не спрашиваем диагноз.", nil)
	b.ask(ctx, user.UserId, app)
}

// processInput передаёт ответ заявителя в анкету и продолжает диалог
func (b *Bot) processInput(ctx context.Context, user schemes.User, in survey.Input, expectedState string) {
	app, err := b.store.Draft(user.UserId)
	if err != nil {
		b.fail(ctx, user.UserId, "поиск черновика", err)

		return
	}
	if app == nil {
		b.send(ctx, user.UserId, "Чтобы оставить обращение, нажмите кнопку ниже или наберите /apply.", b.menuKeyboard())

		return
	}

	if expectedState != "" && expectedState != app.State {
		b.send(ctx, user.UserId, "Этот вопрос уже пройден. Продолжим с текущего.", nil)
		b.ask(ctx, user.UserId, app)

		return
	}

	out := b.engine.Accept(app, in)
	if !out.Accepted {
		b.send(ctx, user.UserId, out.Error, nil)
		b.ask(ctx, user.UserId, app)

		return
	}

	if err := b.store.Save(app); err != nil {
		b.fail(ctx, user.UserId, "сохранение ответа", err)

		return
	}

	switch {
	case out.Stop1:
		b.handleStop1(ctx, user, app)

		return
	case out.Declined:
		app.Status = models.StatusCancelled
		if err := b.store.Save(app); err != nil {
			b.log.Warn("не удалось сохранить отказ от согласия", zap.Error(err))
		}
		b.logEvent(app, "consent_declined", "заявитель не дал согласия на обработку данных")
		b.send(ctx, user.UserId, declinedText(b.cfg), b.menuKeyboard())

		return
	case out.Restart:
		b.restart(ctx, user, app)

		return
	case out.Completed:
		b.submit(ctx, user, app)

		return
	}

	if out.Note != "" {
		b.send(ctx, user.UserId, out.Note, nil)
	}

	if app.State == survey.StateDone && app.Stop1Mark == survey.Stop1Mark {
		b.finishStop1(ctx, user, app)

		return
	}

	b.ask(ctx, user.UserId, app)
}

// handleStop1 выполняет протокол STOP-1: останавливает навигацию, немедленно
// оповещает координатора и запрашивает только контакты для обратной связи
func (b *Bot) handleStop1(ctx context.Context, user schemes.User, app *models.Application) {
	if app.PublicID == "" {
		if err := b.assignPublicID(app); err != nil {
			b.log.Error("не удалось присвоить номер обращения", zap.Error(err))
		}
	}

	b.engine.StartStop1Contacts(app)
	if err := b.store.Save(app); err != nil {
		b.log.Error("не удалось сохранить карточку STOP-1", zap.Error(err))
	}
	b.logEvent(app, "stop1", "выявлен признак "+app.Stop1SignID+" по перечню версии "+app.Stop1PolicyVersion)

	b.send(ctx, user.UserId, stop1Text(), nil)

	// Немедленное оповещение: координатор должен узнать о признаке сразу,
	// не дожидаясь, пока заявитель допишет контакты
	b.alertCoordinator(ctx, app, "STOP-1: выявлен признак экстренного состояния. "+
		"Заявителю названы 103 и 112, контакты уточняются.")

	if app.State == survey.StateDone {
		b.finishStop1(ctx, user, app)

		return
	}

	b.ask(ctx, user.UserId, app)
}

// finishStop1 завершает ветку STOP-1 после сбора контактов
func (b *Bot) finishStop1(ctx context.Context, user schemes.User, app *models.Application) {
	if app.Status == models.StatusStop1 {
		// Ветка уже завершена: повторно карточку не передаём
		return
	}

	app.Status = models.StatusStop1
	action := b.createAction(app, models.ActionAfterEmergency, b.workingDeadline(time.Now()))
	b.applyNextAction(app, action)

	if err := b.store.Save(app); err != nil {
		b.log.Error("не удалось сохранить контакты по STOP-1", zap.Error(err))
	}
	b.logEvent(app, "stop1_contacts", "контакты заявителя записаны")

	b.send(ctx, user.UserId, stop1ClosingText(b.cfg, app), b.menuKeyboard())
	b.notifyCoordinator(ctx, app)
}

// restart очищает ответы анкеты, сохраняя согласие и отметку фильтра
func (b *Bot) restart(ctx context.Context, user schemes.User, app *models.Application) {
	app.ApplicantName = ""
	app.ApplicantPhone = ""
	app.Relation = ""
	app.WardName = ""
	app.WardAge = 0
	app.WardKnows = ""
	app.District = ""
	app.Address = ""
	app.Issue = ""
	app.Mobility = ""
	app.Hygiene = ""
	app.Food = ""
	app.Caregiver = ""
	app.CaregiverLoad = ""
	app.MedicalNeed = ""
	app.SocialServices = ""
	app.PreviousRequests = ""
	app.OtherSpheres = ""
	app.ContactPersonType = ""
	app.ContactPersonName = ""
	app.ContactPersonPhone = ""
	app.ContactTime = ""
	app.State = survey.StateApplicantName

	if err := b.store.Save(app); err != nil {
		b.fail(ctx, user.UserId, "очистка анкеты", err)

		return
	}

	b.send(ctx, user.UserId, "Хорошо, заполним анкету заново.", nil)
	b.ask(ctx, user.UserId, app)
}

// submit завершает обращение: присваивает номер, считает маршрут, передаёт координатору
func (b *Bot) submit(ctx context.Context, user schemes.User, app *models.Application) {
	if app.PublicID == "" {
		if err := b.assignPublicID(app); err != nil {
			b.fail(ctx, user.UserId, "нумерация обращения", err)

			return
		}
	}

	b.markRepeat(app)

	decision := survey.Route(app, b.policies.Routing)
	app.RouteHint = decision.Route
	app.RouteRuleID = decision.RuleID
	app.RouteReason = decision.Reason
	app.RoutePolicyVersion = decision.PolicyVersion
	app.RouteNote = decision.Note
	app.Status = models.StatusSubmitted
	app.State = survey.StateDone
	submitted := time.Now()
	app.SubmittedAt = &submitted

	// Контрольные точки процесса живут в базе, а не в памяти процесса
	initial := b.createAction(app, models.ActionInitialContact, b.workingDeadline(submitted))
	b.createAction(app, models.ActionFollowUp7d, b.followUpDeadline(submitted, b.cfg.Intake.FollowUp7Days))
	b.createAction(app, models.ActionFollowUp30d, b.followUpDeadline(submitted, b.cfg.Intake.FollowUp30Days))
	b.applyNextAction(app, initial)

	if err := b.store.Save(app); err != nil {
		b.fail(ctx, user.UserId, "сохранение обращения", err)

		return
	}

	routeNote := decision.Route
	if routeNote == "" {
		routeNote = "маршрут не рассчитан (" + decision.Note + ")"
	}
	b.logEvent(app, "submitted", "обращение передано координатору, "+routeNote)

	b.send(ctx, user.UserId, closingText(b.cfg, app), b.menuKeyboard())
	b.notifyCoordinator(ctx, app)
}

// assignPublicID присваивает обращению публичный номер
func (b *Bot) assignPublicID(app *models.Application) error {
	publicID, err := b.store.NextPublicID(time.Now())
	if err != nil {
		return err
	}
	app.PublicID = publicID

	return nil
}

// cancelDraft прерывает заполнение анкеты
func (b *Bot) cancelDraft(ctx context.Context, user schemes.User) {
	app, err := b.store.Draft(user.UserId)
	if err != nil {
		b.fail(ctx, user.UserId, "поиск черновика", err)

		return
	}
	if app == nil {
		b.send(ctx, user.UserId, "Сейчас нет незаполненной анкеты.", b.menuKeyboard())

		return
	}

	app.Status = models.StatusCancelled
	if err := b.store.Save(app); err != nil {
		b.fail(ctx, user.UserId, "отмена анкеты", err)

		return
	}
	b.logEvent(app, "cancelled", "заявитель прервал заполнение")

	b.send(ctx, user.UserId, "Анкета отменена. Чтобы начать заново — /apply.", b.menuKeyboard())
}

// showMyApplications показывает обращения заявителя
func (b *Bot) showMyApplications(ctx context.Context, user schemes.User) {
	apps, err := b.store.ByUser(user.UserId, 5)
	if err != nil {
		b.fail(ctx, user.UserId, "выборка обращений", err)

		return
	}
	if len(apps) == 0 {
		b.send(ctx, user.UserId, "У вас пока нет отправленных обращений.", b.menuKeyboard())

		return
	}

	var sb strings.Builder
	sb.WriteString("Ваши обращения:\n\n")
	for _, a := range apps {
		sb.WriteString(a.PublicID + " от " + a.CreatedAt.Format("02.01.2006") + " — " + statusLabel(a.Status) + "\n")
	}
	sb.WriteString("\nЕсли по обращению неделю нет обратной связи — напишите нам ещё раз.")

	b.send(ctx, user.UserId, sb.String(), b.menuKeyboard())
}

// showRecent показывает координатору последние обращения
func (b *Bot) showRecent(ctx context.Context, user schemes.User) {
	role := b.cfg.Role(user.UserId)
	if !config.CanSeeCases(role) {
		b.denyAccess(ctx, user, "list_applications")

		return
	}

	apps, err := b.store.Recent(10)
	if err != nil {
		b.fail(ctx, user.UserId, "выборка обращений", err)

		return
	}
	if len(apps) == 0 {
		b.send(ctx, user.UserId, "Обращений пока нет.", nil)

		return
	}

	var sb strings.Builder
	sb.WriteString("Последние обращения:\n\n")
	for _, a := range apps {
		sb.WriteString(a.PublicID + " · " + a.CreatedAt.Format("02.01.2006 15:04") + " · " + statusLabel(a.Status))
		if a.RouteHint != "" {
			sb.WriteString(" · " + a.RouteHint)
		}
		sb.WriteString("\n")
	}
	sb.WriteString("\nКарточка целиком: /card НОМЕР")

	b.send(ctx, user.UserId, sb.String(), nil)
}

// showCard показывает карточку обращения в объёме, доступном роли
func (b *Bot) showCard(ctx context.Context, user schemes.User, publicID string) {
	role := b.cfg.Role(user.UserId)
	if !config.CanSeeCases(role) {
		b.denyAccess(ctx, user, "view_card")

		return
	}
	if publicID == "" {
		b.send(ctx, user.UserId, "Укажите номер обращения: /card MAX-20260819-0001", nil)

		return
	}

	app, err := b.store.ByPublicID(strings.ToUpper(publicID))
	if err != nil {
		b.fail(ctx, user.UserId, "поиск обращения", err)

		return
	}
	if app == nil {
		b.send(ctx, user.UserId, "Обращение с номером "+publicID+" не найдено.", nil)

		return
	}

	// Каждый просмотр карточки фиксируется в журнале случая
	b.logEvent(app, "card_viewed", fmt.Sprintf("роль %s, пользователь MAX %d", role, user.UserId))

	if config.CanSeeFullCard(role) {
		b.send(ctx, user.UserId, survey.Card(app), nil)

		return
	}

	b.send(ctx, user.UserId, survey.CardForViewer(app), nil)
}

// denyAccess отказывает в доступе и фиксирует попытку
func (b *Bot) denyAccess(ctx context.Context, user schemes.User, operation string) {
	b.log.Warn("отказано в доступе к сведениям об обращениях",
		zap.String("operation", operation), zap.Int64("user_id", user.UserId))
	b.send(ctx, user.UserId, "Эта команда доступна только сотрудникам службы. "+
		"Если вы координатор, попросите администратора добавить ваш идентификатор в настройки доступа: /id", nil)
}

// showTasks показывает открытые действия по случаям
func (b *Bot) showTasks(ctx context.Context, user schemes.User) {
	role := b.cfg.Role(user.UserId)
	if !config.CanSeeCases(role) {
		b.denyAccess(ctx, user, "list_tasks")

		return
	}

	actions, err := b.store.OpenActions(20)
	if err != nil {
		b.fail(ctx, user.UserId, "выборка действий", err)

		return
	}
	if len(actions) == 0 {
		b.send(ctx, user.UserId, "Открытых действий нет.", nil)

		return
	}

	now := time.Now()
	var sb strings.Builder
	sb.WriteString("Открытые действия:\n\n")
	for i := range actions {
		action := &actions[i]
		mark := " "
		if action.DueAt.Before(now) {
			mark = "просрочено, "
		}
		sb.WriteString(fmt.Sprintf("#%d · %s · %s\n%s%s\n\n",
			action.ID, action.PublicID, action.Title(),
			mark, "срок "+action.DueAt.Format("02.01.2006 15:04")))
	}
	sb.WriteString("Закрыть действие: /done НОМЕР результат")

	b.send(ctx, user.UserId, sb.String(), nil)
}

// completeTask закрывает действие с указанием результата
func (b *Bot) completeTask(ctx context.Context, user schemes.User, argument string) {
	role := b.cfg.Role(user.UserId)
	if !config.CanCompleteActions(role) {
		b.denyAccess(ctx, user, "complete_action")

		return
	}

	fields := strings.Fields(argument)
	if len(fields) == 0 {
		b.send(ctx, user.UserId, "Укажите номер действия: /done 12 связались, помощь началась", nil)

		return
	}

	id, err := strconv.ParseUint(strings.TrimPrefix(fields[0], "#"), 10, 64)
	if err != nil {
		b.send(ctx, user.UserId, "Номер действия должен быть числом: /done 12 результат", nil)

		return
	}

	action, err := b.store.ActionByID(uint(id))
	if err != nil {
		b.fail(ctx, user.UserId, "поиск действия", err)

		return
	}
	if action == nil {
		b.send(ctx, user.UserId, fmt.Sprintf("Действие #%d не найдено.", id), nil)

		return
	}
	if action.Status != models.ActionStatusOpen {
		b.send(ctx, user.UserId, fmt.Sprintf("Действие #%d уже закрыто.", id), nil)

		return
	}

	result := strings.TrimSpace(strings.TrimPrefix(argument, fields[0]))
	if result == "" {
		b.send(ctx, user.UserId, "Опишите результат: /done "+fields[0]+" связались, помощь началась", nil)

		return
	}

	completed := time.Now()
	action.Status = models.ActionStatusDone
	action.Result = result
	action.CompletedAt = &completed
	action.CompletedBy = user.UserId

	if err := b.store.SaveAction(action); err != nil {
		b.fail(ctx, user.UserId, "закрытие действия", err)

		return
	}

	if err := b.store.LogEvent(action.ApplicationID, "action_completed",
		fmt.Sprintf("%s: %s (пользователь MAX %d)", action.Type, result, user.UserId)); err != nil {
		b.log.Warn("не удалось записать закрытие действия", zap.Error(err))
	}

	b.send(ctx, user.UserId, fmt.Sprintf("Действие #%d закрыто: %s", id, result), nil)
}

// RemindDueActions напоминает координаторам о наступивших сроках.
//
// Метод вызывается по расписанию: контрольные точки хранятся в базе,
// поэтому напоминания переживают перезапуск приложения.
func (b *Bot) RemindDueActions(ctx context.Context, now time.Time, repeatAfter time.Duration) int {
	actions, err := b.store.DueActions(now, now.Add(-repeatAfter), 20)
	if err != nil {
		b.log.Error("не удалось выбрать наступившие действия", zap.Error(err))

		return 0
	}

	reminded := 0
	for i := range actions {
		action := &actions[i]

		text := fmt.Sprintf("Наступил срок действия по обращению %s\n#%d · %s\nСрок: %s\nВладелец: %s\n\nЗакрыть: /done %d результат",
			action.PublicID, action.ID, action.Title(),
			action.DueAt.Format("02.01.2006 15:04"), action.Owner, action.ID)

		if !b.sendToCoordinators(ctx, text) {
			continue
		}

		if err := b.store.MarkReminded(action, now); err != nil {
			b.log.Warn("не удалось отметить напоминание", zap.Error(err))
		}
		reminded++
	}

	return reminded
}

// sendToCoordinators отправляет сообщение получателям карточек
func (b *Bot) sendToCoordinators(ctx context.Context, text string) bool {
	delivered := false

	if b.cfg.Coordinator.ChatID != 0 {
		if err := b.outbox.Send(ctx, Message{ChatID: b.cfg.Coordinator.ChatID, Text: text}); err != nil {
			b.log.Error("не удалось отправить напоминание в чат координаторов", zap.Error(err))
		} else {
			delivered = true
		}
	}

	for _, id := range b.cfg.Coordinator.UserIDs {
		if err := b.outbox.Send(ctx, Message{UserID: id, Text: text}); err != nil {
			b.log.Error("не удалось отправить напоминание координатору",
				zap.Int64("user_id", id), zap.Error(err))
		} else {
			delivered = true
		}
	}

	return delivered
}

// notifyCoordinator передаёт карточку обращения координаторам
func (b *Bot) notifyCoordinator(ctx context.Context, app *models.Application) {
	b.toCoordinators(ctx, survey.Card(app), "card_sent", app)
}

// alertCoordinator отправляет короткое срочное оповещение по случаю
func (b *Bot) alertCoordinator(ctx context.Context, app *models.Application, text string) {
	b.toCoordinators(ctx, app.PublicID+"\n"+text, "alert_sent", app)
}

// toCoordinators рассылает сообщение получателям карточек и фиксирует результат.
//
// В журнал пишется только факт передачи: содержание карточки в логи не попадает.
func (b *Bot) toCoordinators(ctx context.Context, text, eventType string, app *models.Application) {
	if b.cfg.Coordinator.ChatID == 0 && len(b.cfg.Coordinator.UserIDs) == 0 {
		b.log.Warn("получатель карточек не настроен: укажите coordinator.chat_id или coordinator.user_ids",
			zap.String("application", app.PublicID))
		b.logEvent(app, "delivery_failed", "получатель карточек не настроен")

		return
	}

	delivered := 0
	if b.cfg.Coordinator.ChatID != 0 {
		if err := b.outbox.Send(ctx, Message{ChatID: b.cfg.Coordinator.ChatID, Text: text}); err != nil {
			b.log.Error("не удалось отправить карточку в чат координаторов",
				zap.String("application", app.PublicID), zap.Error(err))
		} else {
			delivered++
		}
	}

	for _, id := range b.cfg.Coordinator.UserIDs {
		if err := b.outbox.Send(ctx, Message{UserID: id, Text: text}); err != nil {
			b.log.Error("не удалось отправить карточку координатору",
				zap.Int64("user_id", id), zap.String("application", app.PublicID), zap.Error(err))
		} else {
			delivered++
		}
	}

	if delivered == 0 {
		// Передача не состоялась: действие остаётся открытым, координатору
		// напомнят повторно, а разрыв виден в журнале случая
		b.logEvent(app, "delivery_failed", "ни один получатель не принял сообщение")

		return
	}

	b.logEvent(app, eventType, fmt.Sprintf("получателей: %d", delivered))
}

// logEvent записывает событие в журнал обращения
func (b *Bot) logEvent(app *models.Application, eventType, details string) {
	if err := b.store.LogEvent(app.ID, eventType, details); err != nil {
		b.log.Warn("не удалось записать событие", zap.Error(err))
	}
}

// fail сообщает заявителю о технической ошибке и пишет её в лог
func (b *Bot) fail(ctx context.Context, userID int64, operation string, err error) {
	b.log.Error("ошибка обработки обращения", zap.String("operation", operation), zap.Error(err))

	text := "Произошла техническая ошибка. Попробуйте ещё раз."
	if b.cfg.Organization.Phone != "" {
		text += " Если не получится — позвоните нам: " + b.cfg.Organization.Phone
	}
	b.send(ctx, userID, text, nil)
}

func statusLabel(status models.Status) string {
	switch status {
	case models.StatusSubmitted:
		return "передано координатору"
	case models.StatusStop1:
		return "STOP-1, координатор свяжется отдельно"
	case models.StatusCancelled:
		return "отменено"
	default:
		return "заполняется"
	}
}

// markRepeat определяет повторность обращения.
//
// Совпадения одного лишь идентификатора MAX недостаточно: с одного аккаунта
// обращаются о разных людях. Repeat ставится только при совпадении подопечного,
// иначе координатор видит пометку «возможно, повторное» и решает сам.
func (b *Bot) markRepeat(app *models.Application) {
	previous, err := b.store.PreviousApplications(app.UserID, 10)
	if err != nil {
		b.log.Warn("не удалось проверить прошлые обращения", zap.Error(err))

		return
	}

	app.Repeat = false
	app.PossibleRepeat = len(previous) > 0

	ward := normalizeName(app.WardName)
	for i := range previous {
		if previous[i].ID == app.ID {
			continue
		}
		if ward != "" && normalizeName(previous[i].WardName) == ward {
			app.Repeat = true
			app.PossibleRepeat = false
			app.PreviousCase = previous[i].PublicID

			return
		}
		if app.PreviousCase == "" {
			app.PreviousCase = previous[i].PublicID
		}
	}
}

// normalizeName приводит ФИО к виду, пригодному для сравнения
func normalizeName(name string) string {
	return strings.Join(strings.Fields(strings.ToLower(strings.ReplaceAll(name, "ё", "е"))), " ")
}

// createAction создаёт действие по случаю с указанным сроком
func (b *Bot) createAction(app *models.Application, actionType string, due time.Time) *models.Action {
	action := &models.Action{
		ApplicationID: app.ID,
		PublicID:      app.PublicID,
		Type:          actionType,
		Owner:         b.coordinatorOwner(),
		DueAt:         due,
		Status:        models.ActionStatusOpen,
	}
	action.Description = action.Title()

	if err := b.store.CreateAction(action); err != nil {
		b.log.Error("не удалось создать действие по случаю",
			zap.String("application", app.PublicID), zap.String("type", actionType), zap.Error(err))

		return nil
	}

	return action
}

// applyNextAction переносит ближайшее действие в поля карточки:
// поле «владелец следующего действия» не может быть пустым
func (b *Bot) applyNextAction(app *models.Application, action *models.Action) {
	if action == nil {
		return
	}

	app.NextActionOwner = action.Owner
	due := action.DueAt
	app.NextActionDue = &due
}

// coordinatorOwner возвращает владельца следующего действия
func (b *Bot) coordinatorOwner() string {
	return "координатор " + b.cfg.Organization.Name
}

// workingDeadline возвращает срок нашего действия в рабочих днях
func (b *Bot) workingDeadline(from time.Time) time.Time {
	days := b.cfg.Intake.NextStepWorkingDays
	if days < 1 {
		days = 1
	}

	return b.calendar.NextWorkingDeadline(from, days)
}

// followUpDeadline возвращает срок контрольной точки: календарные дни,
// но не ранее истечения норматива результата в рабочих днях
func (b *Bot) followUpDeadline(from time.Time, calendarDays int) time.Time {
	if calendarDays < 1 {
		calendarDays = 7
	}

	return b.calendar.CalendarDaysDeadline(from, calendarDays, b.cfg.Intake.FollowUpMinWorkingDays)
}
