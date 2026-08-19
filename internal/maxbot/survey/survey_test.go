package survey

import (
	"testing"

	"telegram-emulator/internal/maxbot/models"
)

func testParams() Params {
	return Params{
		Districts: []string{"Автозаводский район", "Центральный район"},
		Stop1Signs: []Sign{
			{ID: "s1", Question: "Человек без сознания?"},
			{ID: "s2", Question: "Тяжело дышать?"},
		},
	}
}

// answer подаёт ответ в анкету и проверяет, что он принят
func answer(t *testing.T, e *Engine, app *models.Application, in Input) Outcome {
	t.Helper()

	out := e.Accept(app, in)
	if !out.Accepted {
		t.Fatalf("ответ не принят на шаге %q: %s", app.State, out.Error)
	}

	return out
}

func TestStop1PassedOpensIntake(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{}
	app.State = e.FirstState(app)

	if app.State != StateStop1 {
		t.Fatalf("анкета должна начинаться с фильтра STOP-1, получено %q", app.State)
	}

	answer(t, e, app, Input{Payload: AnswerNo})
	if app.State != StateStop1 {
		t.Fatalf("после первого признака ожидался следующий вопрос перечня, получено %q", app.State)
	}

	answer(t, e, app, Input{Payload: AnswerNo})
	if app.Stop1Mark != "STOP-2" {
		t.Fatalf("после прохождения перечня ожидалась отметка STOP-2, получено %q", app.Stop1Mark)
	}
	if app.State != StateConsent {
		t.Fatalf("после фильтра ожидалось согласие на обработку данных, получено %q", app.State)
	}
}

func TestStop1TriggeredStopsSurvey(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{}
	app.State = e.FirstState(app)

	out := answer(t, e, app, Input{Payload: AnswerYes})
	if !out.Stop1 {
		t.Fatal("ожидалось срабатывание STOP-1")
	}
	if app.Stop1Mark != "STOP-1" {
		t.Fatalf("ожидалась отметка STOP-1, получено %q", app.Stop1Mark)
	}
	if app.Stop1Sign != "Человек без сознания?" {
		t.Fatalf("должен фиксироваться сработавший признак, получено %q", app.Stop1Sign)
	}
	if app.Stop1At == nil {
		t.Fatal("должно фиксироваться время выявления признака")
	}
	e.StartStop1Contacts(app)
	if app.State != StateStop1Name {
		t.Fatalf("после STOP-1 ожидался сбор контактов, получено %q", app.State)
	}
	answer(t, e, app, Input{Text: "Иванова Мария"})
	if app.State != StateStop1Phone {
		t.Fatalf("ожидался вопрос о телефоне, получено %q", app.State)
	}
	answer(t, e, app, Input{Text: "8 900 123-45-67"})
	if app.ApplicantPhone != "+79001234567" {
		t.Fatalf("телефон не нормализован: %q", app.ApplicantPhone)
	}
	if app.State != StateDone {
		t.Fatalf("после сбора контактов анкета завершается, получено %q", app.State)
	}
}

func TestConsentDeclined(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{State: StateConsent}

	out := answer(t, e, app, Input{Payload: AnswerNo})
	if !out.Declined {
		t.Fatal("ожидался отказ от согласия")
	}
	if app.ConsentPD {
		t.Fatal("согласие не должно отмечаться при отказе")
	}
}

func TestSelfRelationSkipsWardQuestions(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{State: StateRelation, ApplicantName: "Петров Пётр Петрович"}

	answer(t, e, app, Input{Payload: RelationSelf})
	if app.State != StateWardAge {
		t.Fatalf("для обращения о себе вопрос об имени подопечного пропускается, получено %q", app.State)
	}
	if app.WardName != "Петров Пётр Петрович" {
		t.Fatalf("имя подопечного должно совпадать с именем заявителя, получено %q", app.WardName)
	}

	answer(t, e, app, Input{Text: "78"})
	if app.State != StateDistrict {
		t.Fatalf("вопрос «знает ли об обращении» пропускается, получено %q", app.State)
	}
}

func TestCaregiverLoadSkippedWithoutCaregiver(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{State: StateCaregiver}

	answer(t, e, app, Input{Payload: CaregiverNone})
	if app.State != StateMedical {
		t.Fatalf("без ухаживающего вопрос о нагрузке пропускается, получено %q", app.State)
	}
}

func TestValidation(t *testing.T) {
	e := NewEngine(testParams())

	cases := []struct {
		name  string
		app   *models.Application
		input Input
	}{
		{"возраст не число", &models.Application{State: StateWardAge}, Input{Text: "примерно 80"}},
		{"телефон не распознан", &models.Application{State: StatePhone}, Input{Text: "позвоните мне"}},
		{"описание слишком короткое", &models.Application{State: StateIssue}, Input{Text: "плохо"}},
		{"вариант не из списка", &models.Application{State: StateMobility}, Input{Payload: "maybe"}},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			out := e.Accept(c.app, c.input)
			if out.Accepted {
				t.Fatal("некорректный ответ не должен приниматься")
			}
			if out.Error == "" {
				t.Fatal("заявителю нужно объяснить, что не так")
			}
		})
	}
}

func TestSpheresToggle(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{State: StateSpheres}

	answer(t, e, app, Input{Payload: SphereDocuments})
	answer(t, e, app, Input{Payload: SphereTransport})
	if app.State != StateSpheres {
		t.Fatalf("множественный выбор остаётся на том же шаге, получено %q", app.State)
	}
	if len(app.SpheresList()) != 2 {
		t.Fatalf("ожидались две отмеченные сферы, получено %v", app.SpheresList())
	}

	answer(t, e, app, Input{Payload: SphereDocuments})
	if len(app.SpheresList()) != 1 {
		t.Fatalf("повторное нажатие снимает отметку, получено %v", app.SpheresList())
	}

	answer(t, e, app, Input{Payload: "done"})
	if app.State != StateContactPerson {
		t.Fatalf("после «Готово» ожидался следующий вопрос, получено %q", app.State)
	}
}

func TestFullSurveyFlow(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{}
	app.State = e.FirstState(app)

	inputs := []Input{
		{Payload: AnswerNo},  // STOP-1, признак 1
		{Payload: AnswerNo},  // STOP-1, признак 2
		{Payload: AnswerYes}, // согласие на обработку данных
		{Text: "Иванова Мария Петровна"}, // имя заявителя
		{Text: "+7 900 123-45-67"},       // телефон
		{Payload: RelationRelative},      // кем приходится
		{Text: "Иванов Пётр Сергеевич"},  // имя подопечного
		{Text: "82"},         // возраст
		{Payload: AnswerYes}, // знает об обращении
		{Payload: "d0"},      // район
		{Text: "ул. Ленина, д. 10, кв. 5"},            // адрес
		{Text: "Отец слёг после больницы, не встаёт"}, // суть обращения
		{Payload: AbilityCannot},                      // передвижение
		{Payload: AbilityCannot},                      // гигиена
		{Payload: AbilityCannot},                      // питание
		{Payload: CaregiverSometimes},                 // ухаживающий
		{Payload: "half"},                             // нагрузка ухаживающего
		{Payload: AnswerNo},                           // медицинская составляющая
		{Payload: AnswerNo},                           // социальные услуги
		{Payload: "skip"},                             // куда обращались
		{Payload: "done"},                             // дополнительные сферы
		{Payload: "self"},                             // кто на связи
		{Payload: "any"},                              // удобное время
	}

	for i, in := range inputs {
		state := app.State
		out := e.Accept(app, in)
		if !out.Accepted {
			t.Fatalf("шаг %d (%s): ответ не принят: %s", i, state, out.Error)
		}
	}

	if app.State != StateConfirm {
		t.Fatalf("после последнего вопроса ожидалось подтверждение, получено %q", app.State)
	}
	if q := e.Question(app); q == nil || q.State != StateConfirm {
		t.Fatal("на шаге подтверждения должен быть вопрос")
	}

	out := answer(t, e, app, Input{Payload: "submit"})
	if !out.Completed {
		t.Fatal("ожидалось завершение анкеты")
	}

	if app.Stop1Mark != "STOP-2" {
		t.Fatalf("отметка фильтра обязательна, получено %q", app.Stop1Mark)
	}
	if !app.ConsentPD || app.ConsentPDAt == nil {
		t.Fatal("согласие на обработку данных должно быть зафиксировано")
	}
	if app.ApplicantPhone != "+79001234567" {
		t.Fatalf("телефон не нормализован: %q", app.ApplicantPhone)
	}
	if app.District != "Автозаводский район" {
		t.Fatalf("район записан неверно: %q", app.District)
	}
	if app.PreviousRequests != "не обращались" {
		t.Fatalf("пропуск вопроса должен фиксироваться, получено %q", app.PreviousRequests)
	}
}

func TestNormalizePhone(t *testing.T) {
	cases := map[string]string{
		"+7 900 123-45-67":  "+79001234567",
		"8(900)123-45-67":   "+79001234567",
		"9001234567":        "+79001234567",
		"+7 900 123 45 67 ": "+79001234567",
	}

	for input, expected := range cases {
		got, ok := NormalizePhone(input)
		if !ok || got != expected {
			t.Fatalf("NormalizePhone(%q) = %q, %v; ожидалось %q", input, got, ok, expected)
		}
	}

	if _, ok := NormalizePhone("не помню"); ok {
		t.Fatal("текст без цифр не является телефоном")
	}
}

func TestProgress(t *testing.T) {
	e := NewEngine(testParams())
	app := &models.Application{State: StateApplicantName, Relation: RelationSelf}

	current, total := e.Progress(app)
	if current == 0 || total == 0 || current > total {
		t.Fatalf("некорректный прогресс: %d из %d", current, total)
	}
}
