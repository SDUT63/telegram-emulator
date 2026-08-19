// Package survey реализует анкету первичного обращения службы «Точка входа»:
// последовательность вопросов, проверку ответов и переходы между шагами.
//
// Состав вопросов соответствует перечню обязательных полей (глава 13.1),
// алгоритму оператора (приложение 1) и десяти вопросам о потребности
// (приложение 10) методических рекомендаций АНО «СДУТ».
package survey

import (
	"fmt"
	"strings"
	"time"

	"telegram-emulator/internal/maxbot/models"
	"telegram-emulator/internal/maxbot/policy"
)

// Состояния диалога
const (
	StateStop1         = "stop1"
	StateStop1Name     = "stop1_name"
	StateStop1Phone    = "stop1_phone"
	StateConsent       = "consent"
	StateApplicantName = "applicant_name"
	StatePhone         = "applicant_phone"
	StateRelation      = "relation"
	StateWardName      = "ward_name"
	StateWardAge       = "ward_age"
	StateWardKnows     = "ward_knows"
	StateDistrict      = "district"
	StateAddress       = "address"
	StateIssue         = "issue"
	StateMobility      = "mobility"
	StateHygiene       = "hygiene"
	StateFood          = "food"
	StateCaregiver     = "caregiver"
	StateCaregiverLoad = "caregiver_load"
	StateMedical       = "medical"
	StateSocial        = "social"
	StatePrevRequests  = "prev_requests"
	StateSpheres       = "spheres"
	StateContactPerson = "contact_person"
	StateContactName   = "contact_name"
	StateContactPhone  = "contact_phone"
	StateContactTime   = "contact_time"
	StateConfirm       = "confirm"
	StateDone          = "done"
)

// Kind тип вопроса
type Kind string

const (
	// KindChoice — выбор одного варианта
	KindChoice Kind = "choice"
	// KindMulti — выбор нескольких вариантов
	KindMulti Kind = "multi"
	// KindText — свободный текст
	KindText Kind = "text"
	// KindPhone — номер телефона или кнопка «поделиться контактом»
	KindPhone Kind = "phone"
	// KindAge — возраст числом
	KindAge Kind = "age"
)

// Значения ответов об ограничениях самообслуживания
const (
	AbilitySelf    = "self"
	AbilityHelp    = "help"
	AbilityCannot  = "cannot"
	AbilityUnknown = "unknown"
)

// Значения ответа о наличии ухаживающего
const (
	CaregiverDaily     = "daily"
	CaregiverSometimes = "sometimes"
	CaregiverNone      = "none"
	CaregiverUnknown   = "unknown"
)

// Общие значения «да / нет / не знаю»
const (
	AnswerYes     = "yes"
	AnswerNo      = "no"
	AnswerUnknown = "unknown"
)

// Значения ответа о том, кто будет на связи
const (
	ContactPersonSelf  = "self"
	ContactPersonOther = "other"
)

// Значения отношения заявителя к подопечному
const (
	RelationSelf       = "self"
	RelationRelative   = "relative"
	RelationNeighbour  = "neighbour"
	RelationSpecialist = "specialist"
	RelationOther      = "other"
)

// Дополнительные сферы потребности (признак маршрута М5)
const (
	SphereDocuments = "documents"
	SphereTransport = "transport"
	SphereAids      = "aids"
	SphereGuardian  = "guardian"
	SphereFinance   = "finance"
	SphereNone      = "none"
)

// Отметки фильтра, обязательные по каждому обращению
const (
	// Stop1Mark — выявлен признак возможного экстренного состояния
	Stop1Mark = "STOP-1"
	// Stop2Mark — ни один признак не выявлен, случай допущен в навигацию
	Stop2Mark = "STOP-2"
)

// Params параметры анкеты: справочники конфигурации и утверждаемые правила
type Params struct {
	Districts []string
	// Stop1 — перечень признаков экстренного состояния; применяется,
	// только если отмечен как утверждённый
	Stop1 policy.Stop1
	// ConsentText — формулировка согласия на обработку персональных данных.
	// Задаётся организацией: бот не сочиняет юридические формулировки
	ConsentText string
}

// Stop1Signs возвращает признаки перечня, если он утверждён
func (p Params) Stop1Signs() []policy.Stop1Sign {
	if !p.Stop1.Approved {
		return nil
	}

	return p.Stop1.Signs
}

// Option вариант ответа
type Option struct {
	Value string
	Label string
	// Alarm помечает вариант, требующий внимания (используется для оформления кнопки)
	Alarm bool
}

// Question вопрос, который бот показывает заявителю
type Question struct {
	State    string
	Kind     Kind
	Text     string
	Hint     string
	Options  []Option
	Selected []string
	// Contact добавляет кнопку «Отправить мой номер»
	Contact bool
	// Skip добавляет кнопку пропуска необязательного вопроса
	Skip bool
}

// Input ответ заявителя: нажатие кнопки, текст или присланный контакт
type Input struct {
	Payload string
	Text    string
	Phone   string
}

// Outcome результат обработки ответа
type Outcome struct {
	// Accepted — ответ принят и записан в карточку
	Accepted bool
	// Error — сообщение о неверном ответе, показывается заявителю
	Error string
	// Note — пояснение к принятому ответу
	Note string
	// Stop1 — выявлен признак экстренного состояния
	Stop1 bool
	// Completed — анкета заполнена и подтверждена заявителем
	Completed bool
	// Restart — заявитель попросил заполнить анкету заново
	Restart bool
	// Declined — заявитель не дал согласия на обработку данных
	Declined bool
}

type step struct {
	state    string
	kind     Kind
	text     func(app *models.Application, p Params) string
	hint     string
	options  func(p Params) []Option
	contact  bool
	skip     bool
	skipWhen func(app *models.Application) bool
	accept   func(app *models.Application, in Input, p Params) Outcome
}

// Engine машина состояний анкеты
type Engine struct {
	params Params
	steps  []step
	index  map[string]int
}

// NewEngine создаёт машину состояний анкеты
func NewEngine(params Params) *Engine {
	e := &Engine{params: params}
	e.steps = e.buildSteps()
	e.index = make(map[string]int, len(e.steps))
	for i, s := range e.steps {
		e.index[s.state] = i
	}

	return e
}

// Params возвращает параметры анкеты
func (e *Engine) Params() Params {
	return e.params
}

// FirstState возвращает состояние, с которого начинается анкета
func (e *Engine) FirstState(app *models.Application) string {
	if len(e.params.Stop1Signs()) > 0 {
		return StateStop1
	}

	return e.advanceFrom(app, -1)
}

// Question возвращает текущий вопрос анкеты или nil, если анкета завершена
func (e *Engine) Question(app *models.Application) *Question {
	idx, ok := e.index[app.State]
	if !ok {
		return nil
	}

	s := e.steps[idx]
	q := &Question{
		State:   s.state,
		Kind:    s.kind,
		Text:    s.text(app, e.params),
		Hint:    s.hint,
		Contact: s.contact,
		Skip:    s.skip,
	}
	if s.options != nil {
		q.Options = s.options(e.params)
	}
	if s.kind == KindMulti {
		q.Selected = app.SpheresList()
	}

	return q
}

// Accept обрабатывает ответ заявителя и переводит анкету на следующий шаг
func (e *Engine) Accept(app *models.Application, in Input) Outcome {
	idx, ok := e.index[app.State]
	if !ok {
		return Outcome{Error: "Анкета уже завершена. Наберите /start, чтобы начать заново."}
	}

	s := e.steps[idx]
	out := s.accept(app, in, e.params)
	if !out.Accepted {
		return out
	}

	// Шаг STOP-1 и множественный выбор сами управляют переходом
	if app.State == s.state && (s.state == StateStop1 || s.kind == KindMulti) {
		return out
	}

	if app.State == s.state {
		// Ветка STOP-1 идёт в обход обычной анкеты: только имя и телефон
		if s.state == StateStop1Name || s.state == StateStop1Phone {
			e.StartStop1Contacts(app)
		} else {
			app.State = e.advanceFrom(app, idx)
		}
	}

	return out
}

// advanceFrom находит следующий доступный шаг после шага с индексом idx
func (e *Engine) advanceFrom(app *models.Application, idx int) string {
	for i := idx + 1; i < len(e.steps); i++ {
		s := e.steps[i]
		if s.state == StateStop1 && len(e.params.Stop1Signs()) == 0 {
			continue
		}
		if s.state == StateStop1Name || s.state == StateStop1Phone {
			continue
		}
		if s.skipWhen != nil && s.skipWhen(app) {
			continue
		}

		return s.state
	}

	return StateDone
}

// StartStop1Contacts переводит диалог в ветку сбора контактов после STOP-1
func (e *Engine) StartStop1Contacts(app *models.Application) {
	if strings.TrimSpace(app.ApplicantName) == "" {
		app.State = StateStop1Name

		return
	}
	if strings.TrimSpace(app.ApplicantPhone) == "" {
		app.State = StateStop1Phone

		return
	}

	app.State = StateDone
}

// Progress возвращает номер текущего шага и общее число шагов анкеты
func (e *Engine) Progress(app *models.Application) (int, int) {
	counted := func(s step) bool {
		if s.state == StateStop1Name || s.state == StateStop1Phone || s.state == StateConfirm {
			return false
		}

		return s.state != StateStop1 || len(e.params.Stop1Signs()) > 0
	}

	// Условные шаги учитываются в общем числе, чтобы оно не уменьшалось
	// по ходу разговора; в номер текущего шага они не попадают
	total := 0
	for _, s := range e.steps {
		if counted(s) {
			total++
		}
	}

	current := 0
	for _, s := range e.steps {
		if !counted(s) || (s.skipWhen != nil && s.skipWhen(app)) {
			continue
		}
		current++
		if s.state == app.State {
			return current, total
		}
	}

	return 0, total
}

// choice формирует обработчик вопроса с одним вариантом ответа
func choice(options []Option, assign func(app *models.Application, value string)) func(*models.Application, Input, Params) Outcome {
	return func(app *models.Application, in Input, _ Params) Outcome {
		value, ok := matchOption(options, in)
		if !ok {
			return Outcome{Error: "Пожалуйста, выберите один из вариантов кнопкой ниже."}
		}
		assign(app, value)

		return Outcome{Accepted: true}
	}
}

// matchOption сопоставляет ответ заявителя с вариантами: по payload или по тексту
func matchOption(options []Option, in Input) (string, bool) {
	payload := strings.TrimSpace(in.Payload)
	if payload != "" {
		for _, o := range options {
			if o.Value == payload {
				return o.Value, true
			}
		}

		return "", false
	}

	text := strings.ToLower(strings.TrimSpace(in.Text))
	if text == "" {
		return "", false
	}
	for _, o := range options {
		if strings.ToLower(o.Label) == text {
			return o.Value, true
		}
	}

	return "", false
}

// text формирует обработчик вопроса со свободным текстом
func textStep(minLen int, errMsg string, assign func(app *models.Application, value string)) func(*models.Application, Input, Params) Outcome {
	return func(app *models.Application, in Input, _ Params) Outcome {
		value := strings.TrimSpace(in.Text)
		if len([]rune(value)) < minLen {
			return Outcome{Error: errMsg}
		}
		if len([]rune(value)) > 3000 {
			value = string([]rune(value)[:3000])
		}
		assign(app, value)

		return Outcome{Accepted: true}
	}
}

// NormalizePhone приводит телефон к формату +7XXXXXXXXXX
func NormalizePhone(raw string) (string, bool) {
	digits := make([]rune, 0, len(raw))
	for _, r := range raw {
		if r >= '0' && r <= '9' {
			digits = append(digits, r)
		}
	}

	s := string(digits)
	switch {
	case len(s) == 11 && (s[0] == '7' || s[0] == '8'):
		return "+7" + s[1:], true
	case len(s) == 10 && s[0] == '9':
		return "+7" + s, true
	case len(s) >= 11 && len(s) <= 15:
		return "+" + s, true
	}

	return "", false
}

// Label возвращает человекочитаемое значение ответа
func Label(options []Option, value string) string {
	for _, o := range options {
		if o.Value == value {
			return o.Label
		}
	}
	if value == "" {
		return "не указано"
	}

	return value
}

// Наборы вариантов ответа, используемые и анкетой, и карточкой координатора
var (
	// ConsentOptions варианты ответа о согласии на обработку персональных данных
	ConsentOptions = []Option{
		{Value: AnswerYes, Label: "Согласен(на)"},
		{Value: AnswerNo, Label: "Не согласен(на)"},
	}

	// Stop1Options варианты ответа на вопрос перечня STOP-1
	Stop1Options = []Option{
		{Value: AnswerYes, Label: "Да", Alarm: true},
		{Value: AnswerNo, Label: "Нет"},
	}

	// RelationOptions варианты ответа о том, кем заявитель приходится подопечному
	RelationOptions = []Option{
		{Value: RelationSelf, Label: "Обращаюсь о себе"},
		{Value: RelationRelative, Label: "Родственник"},
		{Value: RelationNeighbour, Label: "Сосед, знакомый"},
		{Value: RelationSpecialist, Label: "Специалист организации"},
		{Value: RelationOther, Label: "Иное"},
	}

	// KnowsOptions варианты ответа о том, знает ли подопечный об обращении
	KnowsOptions = []Option{
		{Value: AnswerYes, Label: "Знает"},
		{Value: AnswerNo, Label: "Не знает"},
		{Value: AnswerUnknown, Label: "Не уверен(а)"},
	}

	// AbilityOptions варианты ответа об ограничениях самообслуживания
	AbilityOptions = []Option{
		{Value: AbilitySelf, Label: "Справляется сам"},
		{Value: AbilityHelp, Label: "Только с помощью"},
		{Value: AbilityCannot, Label: "Не может"},
		{Value: AbilityUnknown, Label: "Не знаю"},
	}

	// CaregiverOptions варианты ответа о наличии ухаживающего
	CaregiverOptions = []Option{
		{Value: CaregiverDaily, Label: "Есть, помогает каждый день"},
		{Value: CaregiverSometimes, Label: "Есть, но не каждый день"},
		{Value: CaregiverNone, Label: "Рядом никого нет"},
		{Value: CaregiverUnknown, Label: "Не знаю"},
	}

	// CaregiverLoadOptions варианты ответа о загруженности ухаживающего
	CaregiverLoadOptions = []Option{
		{Value: "little", Label: "До 2 часов в день"},
		{Value: "half", Label: "Полдня"},
		{Value: "full", Label: "Круглосуточно"},
		{Value: AnswerUnknown, Label: "Не знаю"},
	}

	// MedicalOptions варианты ответа о медицинской составляющей
	MedicalOptions = []Option{
		{Value: AnswerYes, Label: "Да, это есть"},
		{Value: AnswerNo, Label: "Нет"},
		{Value: AnswerUnknown, Label: "Не знаю"},
	}

	// SocialOptions варианты ответа о получаемых социальных услугах
	SocialOptions = []Option{
		{Value: AnswerYes, Label: "Да, получает"},
		{Value: AnswerNo, Label: "Нет"},
		{Value: AnswerUnknown, Label: "Не знаю"},
	}

	// SphereOptions дополнительные сферы потребности
	SphereOptions = []Option{
		{Value: SphereDocuments, Label: "Документы, оформление льгот"},
		{Value: SphereTransport, Label: "Транспорт, сопровождение"},
		{Value: SphereAids, Label: "Средства ухода и технические средства"},
		{Value: SphereGuardian, Label: "Опека, законное представительство"},
		{Value: SphereFinance, Label: "Финансовые трудности"},
	}

	// ContactPersonOptions варианты ответа о том, кто будет на связи
	ContactPersonOptions = []Option{
		{Value: ContactPersonSelf, Label: "Я сам(а)"},
		{Value: ContactPersonOther, Label: "Другой человек"},
	}

	// ContactTimeOptions варианты удобного времени для звонка
	ContactTimeOptions = []Option{
		{Value: "any", Label: "Любое время в рабочие часы"},
		{Value: "morning", Label: "Утро, 9:00–12:00"},
		{Value: "day", Label: "День, 12:00–17:00"},
		{Value: "evening", Label: "Вечер, 17:00–20:00"},
	}

	// ConfirmOptions варианты завершения анкеты
	ConfirmOptions = []Option{
		{Value: "submit", Label: "Отправить обращение"},
		{Value: "restart", Label: "Заполнить заново"},
	}
)

func now() *time.Time {
	t := time.Now()

	return &t
}

func fmtStop1Question(app *models.Application, p Params) string {
	signs := p.Stop1Signs()
	if app.Stop1Index < 0 || app.Stop1Index >= len(signs) {
		return "Проверка завершена."
	}

	return fmt.Sprintf("Вопрос %d из %d.\n\n%s",
		app.Stop1Index+1, len(signs), signs[app.Stop1Index].Question)
}

func districtOptions(p Params) []Option {
	options := make([]Option, 0, len(p.Districts)+1)
	for i, d := range p.Districts {
		options = append(options, Option{Value: fmt.Sprintf("d%d", i), Label: d})
	}
	options = append(options, Option{Value: "other", Label: "Другой населённый пункт"})

	return options
}

// DistrictLabel возвращает название района по значению ответа
func DistrictLabel(p Params, value string) string {
	return Label(districtOptions(p), value)
}
