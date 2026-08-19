package bot

import (
	"context"
	"strings"
	"testing"

	"telegram-emulator/internal/maxbot/config"
	"telegram-emulator/internal/maxbot/models"
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
}

func (f *fakeOutbox) Send(_ context.Context, message Message) error {
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
	cfg.Intake.NextStepHours = 24
	cfg.Coordinator.ChatID = -100
	cfg.Stop1.Approved = true
	cfg.Stop1.Signs = []config.Stop1Sign{
		{ID: "s1", Question: "Человек без сознания?"},
		{ID: "s2", Question: "Тяжело дышать?"},
	}

	outbox := &fakeOutbox{}

	return New(cfg, outbox, store, zap.NewNop()), outbox, store
}

func testUser() schemes.User {
	return schemes.User{UserId: 501, Name: "Мария", Username: "maria"}
}

func tap(b *Bot, state, value string) {
	b.HandleUpdate(context.Background(), &schemes.MessageCallbackUpdate{
		Update: schemes.Update{UpdateType: schemes.TypeMessageCallback},
		Callback: schemes.Callback{
			CallbackID: "cb",
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
			Body:      schemes.MessageBody{Mid: "mid", Text: text},
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
			Body:      schemes.MessageBody{Mid: "mid", Text: text},
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
	tap(b, survey.StateContactPerson, "self")
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
	if app.RouteHint != survey.RouteM3 {
		t.Fatalf("ожидался предварительный маршрут М3, получен %q", app.RouteHint)
	}
	if app.NextActionOwner == "" || app.NextActionDue == nil {
		t.Fatal("поле «владелец следующего действия» обязательно")
	}

	if !sender.containsText(app.PublicID) {
		t.Fatal("заявителю называется номер обращения")
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
	b.cfg.Stop1.Approved = false

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

func TestPhoneFromContactAttachment(t *testing.T) {
	vcf := "BEGIN:VCARD\nVERSION:3.0\nFN:Мария\nTEL;TYPE=CELL:+7 900 123-45-67\nEND:VCARD"
	if got := phoneFromVCF(vcf); got != "+79001234567" {
		t.Fatalf("телефон из контакта распознан неверно: %q", got)
	}
}
