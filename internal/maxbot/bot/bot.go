// Package bot связывает анкету «Точка входа» с MAX Bot API.
package bot

import (
	"context"
	"fmt"
	"strings"
	"time"

	"telegram-emulator/internal/maxbot/config"
	"telegram-emulator/internal/maxbot/models"
	"telegram-emulator/internal/maxbot/storage"
	"telegram-emulator/internal/maxbot/survey"

	"github.com/max-messenger/max-bot-api-client-go/schemes"
	"go.uber.org/zap"
)

// Bot обрабатывает обновления MAX и ведёт анкету первичного обращения
type Bot struct {
	cfg    *config.Config
	outbox Outbox
	store  *storage.Storage
	engine *survey.Engine
	log    *zap.Logger
}

// New создаёт бота
func New(cfg *config.Config, outbox Outbox, store *storage.Storage, log *zap.Logger) *Bot {
	signs := make([]survey.Sign, 0, len(cfg.Stop1.Signs))
	for _, s := range cfg.Stop1.Signs {
		signs = append(signs, survey.Sign{ID: s.ID, Question: s.Question})
	}

	return &Bot{
		cfg:    cfg,
		outbox: outbox,
		store:  store,
		log:    log,
		engine: survey.NewEngine(survey.Params{
			Districts:  cfg.Intake.Districts,
			Stop1Signs: signs,
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
		b.handleMessage(ctx, u)
	case *schemes.MessageCallbackUpdate:
		b.handleCallback(ctx, u)
	default:
		b.log.Debug("обновление без обработчика", zap.String("type", string(update.GetUpdateType())))
	}
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
	if len(b.cfg.Stop1.Signs) == 0 || !b.cfg.Stop1.Approved {
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

	repeat, err := b.store.HasClosedApplications(user.UserId)
	if err != nil {
		b.log.Warn("не удалось проверить прошлые обращения", zap.Error(err))
	}

	app = &models.Application{
		Channel:  "max",
		UserID:   user.UserId,
		Username: user.Username,
		Status:   models.StatusDraft,
		Repeat:   repeat,
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

// handleStop1 выполняет протокол STOP-1: остановка навигации и передача координатору
func (b *Bot) handleStop1(ctx context.Context, user schemes.User, app *models.Application) {
	if app.PublicID == "" {
		if err := b.assignPublicID(app); err != nil {
			b.log.Error("не удалось присвоить номер обращения", zap.Error(err))
		}
	}
	app.NextActionOwner = "координатор " + b.cfg.Organization.Name
	due := b.cfg.NextStepDeadline(time.Now())
	app.NextActionDue = &due

	b.engine.StartStop1Contacts(app)
	if err := b.store.Save(app); err != nil {
		b.log.Error("не удалось сохранить карточку STOP-1", zap.Error(err))
	}
	b.logEvent(app, "stop1", "выявлен признак: "+app.Stop1Sign)

	b.send(ctx, user.UserId, stop1Text(), nil)
	b.notifyCoordinator(ctx, app)

	if app.State == survey.StateDone {
		b.finishStop1(ctx, user, app)

		return
	}

	b.ask(ctx, user.UserId, app)
}

// finishStop1 завершает ветку STOP-1 после сбора контактов
func (b *Bot) finishStop1(ctx context.Context, user schemes.User, app *models.Application) {
	app.Status = models.StatusStop1
	if err := b.store.Save(app); err != nil {
		b.log.Error("не удалось сохранить контакты по STOP-1", zap.Error(err))
	}
	b.logEvent(app, "stop1_contacts", "контакты заявителя записаны")

	b.send(ctx, user.UserId, "Спасибо. Координатор свяжется с вами на следующий рабочий день. "+
		"Номер обращения: "+app.PublicID+".\n\nЕсли состояние ухудшится — звоните 103 или 112.", b.menuKeyboard())
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
	app.ContactPerson = ""
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

	route, reason := survey.Route(app)
	app.RouteHint = route
	app.RouteReason = reason
	app.Status = models.StatusSubmitted
	app.State = survey.StateDone
	submitted := time.Now()
	app.SubmittedAt = &submitted
	app.NextActionOwner = "координатор " + b.cfg.Organization.Name
	due := b.cfg.NextStepDeadline(submitted)
	app.NextActionDue = &due

	if err := b.store.Save(app); err != nil {
		b.fail(ctx, user.UserId, "сохранение обращения", err)

		return
	}
	b.logEvent(app, "submitted", "обращение передано координатору, предварительный маршрут "+route)

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
	if !b.cfg.IsCoordinator(user.UserId) {
		b.send(ctx, user.UserId, "Эта команда доступна только координаторам службы.", nil)

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

// showCard показывает координатору карточку обращения
func (b *Bot) showCard(ctx context.Context, user schemes.User, publicID string) {
	if !b.cfg.IsCoordinator(user.UserId) {
		b.send(ctx, user.UserId, "Эта команда доступна только координаторам службы.", nil)

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

	b.send(ctx, user.UserId, survey.Card(app), nil)
}

// notifyCoordinator передаёт карточку обращения в чат координаторов
func (b *Bot) notifyCoordinator(ctx context.Context, app *models.Application) {
	card := survey.Card(app)

	if b.cfg.Coordinator.ChatID == 0 && len(b.cfg.Coordinator.UserIDs) == 0 {
		b.log.Warn("получатель карточек не настроен: укажите coordinator.chat_id или coordinator.user_ids",
			zap.String("application", app.PublicID))

		return
	}

	if b.cfg.Coordinator.ChatID != 0 {
		if err := b.outbox.Send(ctx, Message{ChatID: b.cfg.Coordinator.ChatID, Text: card}); err != nil {
			b.log.Error("не удалось отправить карточку в чат координаторов", zap.Error(err))
		}
	}

	for _, id := range b.cfg.Coordinator.UserIDs {
		if err := b.outbox.Send(ctx, Message{UserID: id, Text: card}); err != nil {
			b.log.Error("не удалось отправить карточку координатору",
				zap.Int64("user_id", id), zap.Error(err))
		}
	}
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
