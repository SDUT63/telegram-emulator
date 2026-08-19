package bot

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"telegram-emulator/internal/maxbot/config"
	"telegram-emulator/internal/maxbot/models"
	"telegram-emulator/internal/maxbot/policy"
	"telegram-emulator/internal/maxbot/storage"
	"telegram-emulator/internal/maxbot/survey"

	"github.com/max-messenger/max-bot-api-client-go/schemes"
	"go.uber.org/zap"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"
)

// fakeOutbox записывает отправленные сообщения вместо обращения к MAX Bot API
type fakeOutbox struct {
	messages []Message
	fail     bool
}

// errFakeSend имитирует отказ MAX Bot API
var errFakeSend = errors.New("сбой отправки")

func (f *fakeOutbox) Send(_ context.Context, message Message) error {
	if f.fail {
		return errFakeSend
	}
	f.messages = append(f.messages, message)

	return nil
}

func (f *fakeOutbox) Answer(_ context.Context, _, _ string) error {
	return nil
}

func (f *fakeOutbox) last() string {
	if len(f.messages) == 0 {
		return ""
	}

	return f.messages[len(f.messages)-1].Text
}

func (f *fakeOutbox) containsText(part string) bool {
	for _, m := range f.messages {
		if strings.Contains(m.Text, part) {
			return true
		}
	}

	return false
}

func newTestBot(t *testing.T) (*Bot, *fakeOutbox, *storage.Storage) {
	t.Helper()

	db, err := gorm.Open(sqlite.Open("file::memory:?cache=shared"), &gorm.Config{
		Logger: gormlogger.Default.LogMode(gormlogger.Silent),
	})
	if err != nil {
		t.Fatalf("не удалось открыть тестовую базу: %v", err)
	}
	store, err := storage.New(db)
	if err != nil {
		t.Fatalf("не удалось подготовить базу: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Logf("ошибка закрытия базы: %v", err)
		}
	})

	cfg := &config.Config{}
	cfg.Organization.Name = "АНО «СДУТ»"
	cfg.Organization.ServiceName = "Точка входа"
	cfg.Organization.Phone = "+7 (8482) 00-00-00"
	cfg.Intake.Districts = []string{"Автозаводский район", "Центральный район"}
	cfg.Intake.NextStepWorkingDays = 1
	cfg.Intake.FollowUp7Days = 7
	cfg.Intake.FollowUpMinWorkingDays = 5
	cfg.Intake.FollowUp30Days = 30
	cfg.Coordinator.ChatID = -100
	cfg.Access.Coordinators = []int64{700}
	cfg.Access.Viewers = []int64{800}
	cfg.Consent.Text = "Нужно ваше согласие на обработку данных. Вы согласны?"
	cfg.Calendar.Timezone = "Europe/Samara"
	cfg.Calendar.Days = []string{"mon", "tue", "wed", "thu", "fri"}
	cfg.Calendar.Start = "09:00"
	cfg.Calendar.End = "18:00"

	workingCalendar, err := cfg.WorkingCalendar()
	if err != nil {
		t.Fatalf("не удалось собрать рабочий календарь: %v", err)
	}

	policies := &policy.Set{
		Stop1: policy.Stop1{
			Version: "test-1", Approved: true,
			ApprovedBy: "заведующий отделением ПМП", ApprovedAt: "2026-08-01",
			Signs: []policy.Stop1Sign{
				{ID: "s1", Question: "Человек без сознания?", Trigger: "yes", Action: policy.ActionStop1},
				{ID: "s2", Question: "Тяжело дышать?", Trigger: "yes", Action: policy.ActionStop1},
			},
		},
		Routing: policy.Routing{
			Version: "test-1", Approved: true,
			Rules: []policy.RoutingRule{
				{ID: "R040", Priority: 70, Conditions: map[string]string{"lost_count": ">=3"},
					Route: "М3", Reason: "утрата по трём позициям"},
			},
		},
		DataCollection: policy.DataCollection{Version: "DC-2026-01"},
	}

	outbox := &fakeOutbox{}

	return New(cfg, outbox, store, policies, workingCalendar, zap.NewNop()), outbox, store
}

func testUser() schemes.User {
	return schemes.User{UserId: 501, Name: "Мария", Username: "maria"}
}

// eventID выдаёт уникальный идентификатор события для тестов
var eventCounter int

func eventID(prefix string) string {
	eventCounter++

	return fmt.Sprintf("%s-%d", prefix, eventCounter)
}

func tap(b *Bot, state, value string) {
	b.HandleUpdate(context.Background(), &schemes.MessageCallbackUpdate{
		Update: schemes.Update{UpdateType: schemes.TypeMessageCallback},
		Callback: schemes.Callback{
			CallbackID: eventID("cb"),
			Payload:    answerPayload(state, value),
			User:       testUser(),
		},
	})
}

func write(b *Bot, text string) {
	b.HandleUpdate(context.Background(), &schemes.MessageCreatedUpdate{
		Update: schemes.Update{UpdateType: schemes.TypeMessageCreated},
		Message: schemes.Message{
			Sender:    testUser(),
			Recipient: schemes.Recipient{UserId: testUser().UserId, ChatId: 900, ChatType: schemes.DIALOG},
			Body:      schemes.MessageBody{Mid: eventID("mid"), Text: text},
		},
	})
}

// writeInGroup отправляет сообщение в групповой чат, где присутствует бот
func writeInGroup(b *Bot, text string) {
	b.HandleUpdate(context.Background(), &schemes.MessageCreatedUpdate{
		Update: schemes.Update{UpdateType: schemes.TypeMessageCreated},
		Message: schemes.Message{
			Sender:    testUser(),
			Recipient: schemes.Recipient{ChatId: -100, ChatType: schemes.CHAT},
			Body:      schemes.MessageBody{Mid: eventID("mid"), Text: text},
		},
	})
}

func TestIntakeCollectsContactsAndNotifiesCoordinator(t *testing.T) {
	b, sender, store := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateConsent, survey.AnswerYes)
	write(b, "Иванова Мария Петровна")
	write(b, "+7 900 123-45-67")
	tap(b, survey.StateRelation, survey.RelationRelative)
	write(b, "Иванов Пётр Сергеевич")
	write(b, "82")
	tap(b, survey.StateWardKnows, survey.AnswerYes)
	tap(b, survey.StateDistrict, "d0")
	write(b, "ул. Ленина, д. 10, кв. 5")
	write(b, "Отец слёг после выписки, не встаёт и не может помыться")
	tap(b, survey.StateMobility, survey.AbilityCannot)
	tap(b, survey.StateHygiene, survey.AbilityCannot)
	tap(b, survey.StateFood, survey.AbilityCannot)
	tap(b, survey.StateCaregiver, survey.CaregiverSometimes)
	tap(b, survey.StateCaregiverLoad, "half")
	tap(b, survey.StateMedical, survey.AnswerNo)
	tap(b, survey.StateSocial, survey.AnswerNo)
	tap(b, survey.StatePrevRequests, "skip")
	tap(b, survey.StateSpheres, "done")
	tap(b, survey.StateContactPerson, survey.ContactPersonSelf)
	tap(b, survey.StateContactTime, "any")

	if !sender.containsText("Проверьте, пожалуйста, что записано верно") {
		t.Fatal("перед отправкой заявителю показывается сводка анкеты")
	}

	tap(b, survey.StateConfirm, "submit")

	apps, err := store.Recent(10)
	if err != nil {
		t.Fatalf("ошибка выборки обращений: %v", err)
	}
	if len(apps) != 1 {
		t.Fatalf("ожидалось одно обращение, получено %d", len(apps))
	}

	app := apps[0]
	if app.Status != models.StatusSubmitted {
		t.Fatalf("ожидался статус submitted, получен %q", app.Status)
	}
	if app.PublicID == "" {
		t.Fatal("обращению должен присваиваться номер")
	}
	if app.ApplicantPhone != "+79001234567" {
		t.Fatalf("контактный телефон не сохранён: %q", app.ApplicantPhone)
	}
	if app.ApplicantName == "" || app.WardName == "" || app.Address == "" {
		t.Fatal("контактные сведения должны быть сохранены полностью")
	}
	if app.Stop1Mark != "STOP-2" {
		t.Fatalf("отметка фильтра обязательна, получено %q", app.Stop1Mark)
	}
	if app.RouteHint != "М3" || app.RouteRuleID != "R040" {
		t.Fatalf("ожидался маршрут М3 по правилу R040, получено %q / %q", app.RouteHint, app.RouteRuleID)
	}
	if app.DataPolicyVersion != "DC-2026-01" {
		t.Fatalf("в карточке должна фиксироваться версия профиля данных, получено %q", app.DataPolicyVersion)
	}
	if app.NextActionOwner == "" || app.NextActionDue == nil {
		t.Fatal("поле «владелец следующего действия» обязательно")
	}

	if !sender.containsText(app.PublicID) {
		t.Fatal("заявителю называется номер обращения")
	}

	// Срок нашего действия считается по рабочему календарю
	if app.NextActionDue == nil || !workingDay(t, b, *app.NextActionDue) {
		t.Fatalf("срок следующего действия должен приходиться на рабочий день: %v", app.NextActionDue)
	}

	// Контрольные точки процесса созданы и хранятся в базе
	actions, err := store.ActionsByApplication(app.ID)
	if err != nil {
		t.Fatalf("ошибка выборки действий: %v", err)
	}
	created := map[string]bool{}
	for _, action := range actions {
		created[action.Type] = true
		if action.Status != models.ActionStatusOpen || action.Owner == "" {
			t.Fatalf("действие должно быть открытым и иметь владельца: %+v", action)
		}
	}
	for _, required := range []string{models.ActionInitialContact, models.ActionFollowUp7d, models.ActionFollowUp30d} {
		if !created[required] {
			t.Fatalf("не создано действие %s", required)
		}
	}

	var cardSent bool
	for _, m := range sender.messages {
		if m.ChatID == -100 && strings.Contains(m.Text, "НОВОЕ ОБРАЩЕНИЕ") {
			cardSent = true
		}
	}
	if !cardSent {
		t.Fatal("карточка обращения должна уходить в чат координаторов")
	}
}

func TestStop1StopsIntakeAndAsksContacts(t *testing.T) {
	b, sender, store := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerYes)

	if !sender.containsText("вызовите скорую: 103") {
		t.Fatal("при STOP-1 бот называет номера экстренных служб")
	}

	write(b, "Иванова Мария")
	write(b, "89001234567")

	apps, err := store.Recent(10)
	if err != nil {
		t.Fatalf("ошибка выборки обращений: %v", err)
	}
	if len(apps) != 1 {
		t.Fatalf("ожидалось одно обращение, получено %d", len(apps))
	}

	app := apps[0]
	if app.Status != models.StatusStop1 {
		t.Fatalf("ожидался статус stop1, получен %q", app.Status)
	}
	if app.Stop1Sign == "" || app.Stop1At == nil {
		t.Fatal("признак и время выявления должны фиксироваться")
	}
	if app.ApplicantPhone != "+79001234567" {
		t.Fatalf("контакт для обратного звонка не сохранён: %q", app.ApplicantPhone)
	}
	if !sender.containsText("STOP-1 — ТРЕБУЕТСЯ НЕМЕДЛЕННАЯ РЕАКЦИЯ") {
		t.Fatal("координатор должен получить карточку STOP-1")
	}
}

func TestLineClosedWithoutApprovedStop1List(t *testing.T) {
	b, sender, store := newTestBot(t)
	b.policies.Stop1.Approved = false

	write(b, "/apply")

	if !strings.Contains(sender.last(), "пока не открыт") {
		t.Fatalf("без утверждённого перечня линия не открывается, получено: %s", sender.last())
	}

	apps, err := store.Recent(10)
	if err != nil {
		t.Fatalf("ошибка выборки обращений: %v", err)
	}
	if len(apps) != 0 {
		t.Fatal("обращение не должно создаваться при закрытой линии")
	}
}

func TestConsentDeclinedClosesApplication(t *testing.T) {
	b, sender, store := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateConsent, survey.AnswerNo)

	if !strings.Contains(sender.last(), "Без согласия") {
		t.Fatalf("бот объясняет последствия отказа, получено: %s", sender.last())
	}

	draft, err := store.Draft(testUser().UserId)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft != nil {
		t.Fatal("после отказа черновик закрывается")
	}
}

func TestOutdatedButtonDoesNotBreakSurvey(t *testing.T) {
	b, sender, _ := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateConsent, survey.AnswerYes)

	// Заявитель нажал кнопку в старом сообщении
	tap(b, survey.StateConsent, survey.AnswerYes)

	if !sender.containsText("Этот вопрос уже пройден") {
		t.Fatal("нажатие устаревшей кнопки не должно ломать анкету")
	}
}

func TestGroupChatMessagesAreIgnored(t *testing.T) {
	b, sender, store := newTestBot(t)

	writeInGroup(b, "обсуждаем случай Иванова")

	if len(sender.messages) != 0 {
		t.Fatalf("в групповом чате бот не вмешивается в переписку: %v", sender.messages)
	}

	draft, err := store.Draft(testUser().UserId)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft != nil {
		t.Fatal("сообщение в группе не должно начинать анкету")
	}

	// Служебная команда координатора в группе работает и отвечает в тот же чат
	writeInGroup(b, "/id")
	if len(sender.messages) != 1 || sender.messages[0].ChatID != -100 {
		t.Fatalf("ответ на /id должен уходить в тот же чат: %v", sender.messages)
	}
	if !strings.Contains(sender.last(), "Идентификатор этого чата: -100") {
		t.Fatalf("бот сообщает идентификатор чата, получено: %s", sender.last())
	}
}

// workingDay проверяет, что момент приходится на рабочий день календаря
func workingDay(t *testing.T, b *Bot, moment time.Time) bool {
	t.Helper()

	return b.calendar.IsWorkingDay(moment)
}

func TestDuplicateCallbackIsIgnored(t *testing.T) {
	b, sender, store := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerNo)

	// Повторная доставка одного и того же нажатия
	duplicate := schemes.Callback{
		CallbackID: "cb-duplicate",
		Payload:    answerPayload(survey.StateStop1, survey.AnswerNo),
		User:       testUser(),
	}
	update := &schemes.MessageCallbackUpdate{
		Update:   schemes.Update{UpdateType: schemes.TypeMessageCallback},
		Callback: duplicate,
	}

	before := len(sender.messages)
	b.HandleUpdate(context.Background(), update)
	afterFirst := len(sender.messages)
	b.HandleUpdate(context.Background(), update)
	afterSecond := len(sender.messages)

	if afterFirst == before {
		t.Fatal("первое нажатие должно обрабатываться")
	}
	if afterSecond != afterFirst {
		t.Fatal("повторно доставленное нажатие не должно продвигать анкету")
	}

	draft, err := store.Draft(testUser().UserId)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft == nil || draft.Stop1Mark != survey.Stop2Mark {
		t.Fatalf("анкета должна пройти фильтр ровно один раз: %+v", draft)
	}
}

func TestStop1SendsSingleAlertAndSingleCard(t *testing.T) {
	b, sender, store := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerYes)
	write(b, "Иванова Мария")
	write(b, "89001234567")

	// Повторная попытка завершить ветку не должна порождать вторую карточку
	app, err := store.Draft(testUser().UserId)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if app != nil {
		t.Fatal("после завершения ветки STOP-1 черновика быть не должно")
	}

	alerts, cards := 0, 0
	for _, m := range sender.messages {
		if m.ChatID != -100 {
			continue
		}
		switch {
		case strings.Contains(m.Text, "STOP-1: выявлен признак"):
			alerts++
		case strings.Contains(m.Text, "STOP-1 — ТРЕБУЕТСЯ НЕМЕДЛЕННАЯ РЕАКЦИЯ"):
			cards++
		}
	}

	if alerts != 1 {
		t.Fatalf("координатору уходит ровно одно срочное оповещение, получено %d", alerts)
	}
	if cards != 1 {
		t.Fatalf("координатору уходит ровно одна карточка STOP-1, получено %d", cards)
	}
}

func TestStop1CreatesFollowUpAction(t *testing.T) {
	b, _, store := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerYes)
	write(b, "Иванова Мария")
	write(b, "89001234567")

	apps, err := store.Recent(1)
	if err != nil || len(apps) != 1 {
		t.Fatalf("ожидалось одно обращение: %v %v", apps, err)
	}

	actions, err := store.ActionsByApplication(apps[0].ID)
	if err != nil {
		t.Fatalf("ошибка выборки действий: %v", err)
	}
	if len(actions) != 1 || actions[0].Type != models.ActionAfterEmergency {
		t.Fatalf("после STOP-1 создаётся возврат к вопросу об уходе: %+v", actions)
	}
	if apps[0].NextActionOwner == "" || apps[0].NextActionDue == nil {
		t.Fatal("поле «владелец следующего действия» обязательно и после STOP-1")
	}
}

func TestCardAccessIsRoleBasedAndAudited(t *testing.T) {
	b, sender, store := newTestBot(t)

	app := &models.Application{
		PublicID: "MAX-20260819-0001", UserID: 501, Status: models.StatusSubmitted,
		ApplicantName: "Иванова Мария", ApplicantPhone: "+79001234567",
		Address: "ул. Ленина, 10", Issue: "Отец не встаёт", District: "Автозаводский район",
	}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	// Посторонний пользователь
	b.handleCommand(context.Background(), testUser(), "/card MAX-20260819-0001", schemes.Recipient{})
	if !strings.Contains(sender.last(), "только сотрудникам службы") {
		t.Fatalf("посторонним карточка не выдаётся, получено: %s", sender.last())
	}
	if sender.containsText("+79001234567") {
		t.Fatal("телефон заявителя не должен попадать постороннему")
	}

	// Роль viewer: карточка без контактов
	viewer := schemes.User{UserId: 800, Name: "Наблюдатель"}
	b.handleCommand(context.Background(), viewer, "/card MAX-20260819-0001", schemes.Recipient{})
	if strings.Contains(sender.last(), "+79001234567") || strings.Contains(sender.last(), "ул. Ленина") {
		t.Fatalf("роль viewer не должна видеть контакты и адрес: %s", sender.last())
	}

	// Роль coordinator: карточка целиком
	coordinator := schemes.User{UserId: 700, Name: "Координатор"}
	b.handleCommand(context.Background(), coordinator, "/card MAX-20260819-0001", schemes.Recipient{})
	if !strings.Contains(sender.last(), "+79001234567") {
		t.Fatalf("координатор должен видеть контакты: %s", sender.last())
	}

	// Каждый просмотр записан в журнал случая
	events, err := store.Events(app.ID)
	if err != nil {
		t.Fatalf("ошибка выборки журнала: %v", err)
	}
	views := 0
	for _, event := range events {
		if event.Type == "card_viewed" {
			views++
		}
	}
	if views != 2 {
		t.Fatalf("в журнале должно быть два просмотра карточки, получено %d", views)
	}
}

func TestCoordinatorClosesAction(t *testing.T) {
	b, sender, store := newTestBot(t)
	coordinator := schemes.User{UserId: 700, Name: "Координатор"}

	app := &models.Application{PublicID: "MAX-20260819-0002", UserID: 501, Status: models.StatusSubmitted}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}
	action := &models.Action{
		ApplicationID: app.ID, PublicID: app.PublicID, Type: models.ActionFollowUp7d,
		Owner: "координатор", DueAt: time.Now(), Status: models.ActionStatusOpen,
	}
	if err := store.CreateAction(action); err != nil {
		t.Fatalf("ошибка создания действия: %v", err)
	}

	// Посторонний закрыть действие не может
	b.handleCommand(context.Background(), testUser(), "/done 1 сделано", schemes.Recipient{})
	if !strings.Contains(sender.last(), "только сотрудникам службы") {
		t.Fatalf("закрывать действия вправе только сотрудники: %s", sender.last())
	}

	b.handleCommand(context.Background(), coordinator, "/tasks", schemes.Recipient{})
	if !strings.Contains(sender.last(), "контроль 7-го дня") {
		t.Fatalf("координатор видит открытые действия: %s", sender.last())
	}

	b.handleCommand(context.Background(), coordinator,
		fmt.Sprintf("/done %d помощь началась, соцработник приходит", action.ID), schemes.Recipient{})

	updated, err := store.ActionByID(action.ID)
	if err != nil || updated == nil {
		t.Fatalf("действие не найдено: %v", err)
	}
	if updated.Status != models.ActionStatusDone || updated.Result == "" || updated.CompletedBy != coordinator.UserId {
		t.Fatalf("действие должно закрываться с результатом и автором: %+v", updated)
	}
}

func TestRemindersAreSentOnceUntilRepeatInterval(t *testing.T) {
	b, sender, store := newTestBot(t)

	app := &models.Application{PublicID: "MAX-20260819-0003", UserID: 501, Status: models.StatusSubmitted}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}
	if err := store.CreateAction(&models.Action{
		ApplicationID: app.ID, PublicID: app.PublicID, Type: models.ActionFollowUp7d,
		Owner: "координатор", DueAt: time.Now().Add(-time.Hour), Status: models.ActionStatusOpen,
	}); err != nil {
		t.Fatalf("ошибка создания действия: %v", err)
	}

	now := time.Now()
	if reminded := b.RemindDueActions(context.Background(), now, 24*time.Hour); reminded != 1 {
		t.Fatalf("ожидалось одно напоминание, отправлено %d", reminded)
	}
	if !sender.containsText("Наступил срок действия по обращению MAX-20260819-0003") {
		t.Fatal("координатору уходит напоминание о наступившем сроке")
	}

	if reminded := b.RemindDueActions(context.Background(), now, 24*time.Hour); reminded != 0 {
		t.Fatalf("повторное напоминание в пределах интервала не отправляется, отправлено %d", reminded)
	}
}

func TestDeliveryFailureIsRecorded(t *testing.T) {
	b, sender, store := newTestBot(t)
	sender.fail = true

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerYes)

	// Карточка не доставлена, поэтому обращение остаётся в работе
	app, err := store.Draft(testUser().UserId)
	if err != nil || app == nil {
		t.Fatalf("обращение должно сохраняться независимо от доставки: %v %v", app, err)
	}

	events, err := store.Events(app.ID)
	if err != nil {
		t.Fatalf("ошибка выборки журнала: %v", err)
	}

	var failure bool
	for _, event := range events {
		if event.Type == "delivery_failed" {
			failure = true
		}
	}
	if !failure {
		t.Fatal("несостоявшаяся передача карточки должна попадать в журнал случая")
	}
}

func TestDraftSurvivesRestart(t *testing.T) {
	b, _, store := newTestBot(t)

	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateConsent, survey.AnswerYes)
	write(b, "Иванова Мария Петровна")

	// Новый экземпляр бота на том же хранилище — как после перезапуска процесса
	restarted := New(b.cfg, &fakeOutbox{}, store, b.policies, b.calendar, zap.NewNop())

	draft, err := store.Draft(testUser().UserId)
	if err != nil || draft == nil {
		t.Fatalf("черновик должен сохраняться в базе: %v %v", draft, err)
	}
	if draft.State != survey.StatePhone {
		t.Fatalf("анкета должна продолжиться с того же шага, получено %q", draft.State)
	}

	question := restarted.engine.Question(draft)
	if question == nil || question.State != survey.StatePhone {
		t.Fatal("после перезапуска бот задаёт тот же вопрос")
	}
}

func TestPhoneFromContactAttachment(t *testing.T) {
	vcf := "BEGIN:VCARD\nVERSION:3.0\nFN:Мария\nTEL;TYPE=CELL:+7 900 123-45-67\nEND:VCARD"
	if got := phoneFromVCF(vcf); got != "+79001234567" {
		t.Fatalf("телефон из контакта распознан неверно: %q", got)
	}
}

func TestRepeatRequiresSameWard(t *testing.T) {
	b, _, store := newTestBot(t)

	// Первое обращение — об отце
	fillSurvey(b, "Иванов Пётр Сергеевич")
	// Второе обращение с того же аккаунта — о матери
	fillSurvey(b, "Иванова Анна Ивановна")
	// Третье — снова об отце
	fillSurvey(b, "иванов пётр сергеевич")

	apps, err := store.Recent(10)
	if err != nil || len(apps) != 3 {
		t.Fatalf("ожидалось три обращения: %v %v", len(apps), err)
	}

	third, second, first := apps[0], apps[1], apps[2]

	if first.Repeat || first.PossibleRepeat {
		t.Fatal("первое обращение не может быть повторным")
	}
	if second.Repeat {
		t.Fatal("обращение о другом человеке не является повторным")
	}
	if !second.PossibleRepeat {
		t.Fatal("координатору нужна пометка «возможно, повторное»")
	}
	if !third.Repeat || third.PreviousCase != first.PublicID {
		t.Fatalf("обращение о том же человеке — повторное со ссылкой на прошлый случай: %+v", third)
	}
}

// fillSurvey проходит анкету целиком с указанным именем подопечного
func fillSurvey(b *Bot, wardName string) {
	write(b, "/apply")
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateStop1, survey.AnswerNo)
	tap(b, survey.StateConsent, survey.AnswerYes)
	write(b, "Иванова Мария Петровна")
	write(b, "+7 900 123-45-67")
	tap(b, survey.StateRelation, survey.RelationRelative)
	write(b, wardName)
	write(b, "82")
	tap(b, survey.StateWardKnows, survey.AnswerYes)
	tap(b, survey.StateDistrict, "d0")
	write(b, "ул. Ленина, д. 10, кв. 5")
	write(b, "Не встаёт после выписки, нужна помощь по уходу")
	tap(b, survey.StateMobility, survey.AbilityCannot)
	tap(b, survey.StateHygiene, survey.AbilityCannot)
	tap(b, survey.StateFood, survey.AbilityCannot)
	tap(b, survey.StateCaregiver, survey.CaregiverSometimes)
	tap(b, survey.StateCaregiverLoad, "half")
	tap(b, survey.StateMedical, survey.AnswerNo)
	tap(b, survey.StateSocial, survey.AnswerNo)
	tap(b, survey.StatePrevRequests, "skip")
	tap(b, survey.StateSpheres, "done")
	tap(b, survey.StateContactPerson, survey.ContactPersonSelf)
	tap(b, survey.StateContactTime, "any")
	tap(b, survey.StateConfirm, "submit")
}
