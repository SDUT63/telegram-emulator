// Package models описывает карточку обращения службы «Точка входа».
package models

import (
	"strings"
	"time"
)

// Status статус карточки обращения
type Status string

const (
	// StatusDraft — анкета заполняется, обращение ещё не отправлено
	StatusDraft Status = "draft"
	// StatusSubmitted — анкета заполнена и передана координатору
	StatusSubmitted Status = "submitted"
	// StatusStop1 — выявлен признак STOP-1, обычная навигация остановлена
	StatusStop1 Status = "stop1"
	// StatusCancelled — заявитель прервал заполнение
	StatusCancelled Status = "cancelled"
)

// Application карточка первичного обращения.
//
// Состав полей соответствует перечню обязательных полей методических
// рекомендаций (глава 13.1) и чек-листу первичного обращения (приложение 4).
// Диагноз, сведения о доходах и имуществе не собираются и не хранятся.
type Application struct {
	ID       uint   `gorm:"primaryKey" json:"id"`
	PublicID string `gorm:"uniqueIndex;size:32" json:"public_id"`

	// Канал обращения и идентификация собеседника в мессенджере
	Channel  string `gorm:"size:16;index" json:"channel"`
	UserID   int64  `gorm:"index" json:"user_id"`
	ChatID   int64  `json:"chat_id"`
	Username string `gorm:"size:64" json:"username"`

	// Состояние диалога
	State  string `gorm:"size:32" json:"state"`
	Status Status `gorm:"size:16;index" json:"status"`

	// Фильтр STOP-1 / STOP-2 (обязательная отметка по каждому обращению).
	// Stop1PolicyVersion фиксирует версию перечня, по которой пройден фильтр
	Stop1Index         int        `json:"stop1_index"`
	Stop1Mark          string     `gorm:"size:16" json:"stop1_mark"`
	Stop1SignID        string     `gorm:"size:32" json:"stop1_sign_id"`
	Stop1Sign          string     `gorm:"size:512" json:"stop1_sign"`
	Stop1At            *time.Time `json:"stop1_at"`
	Stop1PolicyVersion string     `gorm:"size:32" json:"stop1_policy_version"`

	// Согласие на обработку персональных данных (согласие на передачу
	// конкретному получателю оформляет координатор, ботом не собирается)
	ConsentPD   bool       `json:"consent_pd"`
	ConsentPDAt *time.Time `json:"consent_pd_at"`

	// Кто обратился и о ком идёт речь
	ApplicantName  string `gorm:"size:255" json:"applicant_name"`
	ApplicantPhone string `gorm:"size:32" json:"applicant_phone"`
	Relation       string `gorm:"size:32" json:"relation"`
	WardName       string `gorm:"size:255" json:"ward_name"`
	WardAge        int    `json:"ward_age"`
	WardKnows      string `gorm:"size:16" json:"ward_knows"`

	// Территория
	District string `gorm:"size:128" json:"district"`
	Address  string `gorm:"size:512" json:"address"`

	// Суть обращения со слов заявителя
	Issue string `gorm:"size:4000" json:"issue"`

	// Ограничения самообслуживания и ухаживающий
	Mobility      string `gorm:"size:16" json:"mobility"`
	Hygiene       string `gorm:"size:16" json:"hygiene"`
	Food          string `gorm:"size:16" json:"food"`
	Caregiver     string `gorm:"size:16" json:"caregiver"`
	CaregiverLoad string `gorm:"size:16" json:"caregiver_load"`

	// Дополнительные сведения, влияющие на выбор маршрута
	MedicalNeed      string `gorm:"size:16" json:"medical_need"`
	SocialServices   string `gorm:"size:16" json:"social_services"`
	PreviousRequests string `gorm:"size:2000" json:"previous_requests"`
	OtherSpheres     string `gorm:"size:255" json:"other_spheres"`

	// Обратная связь: тип, имя и телефон контактного лица хранятся раздельно,
	// чтобы телефон можно было нормализовать и использовать для звонка
	ContactPersonType  string `gorm:"size:16" json:"contact_person_type"`
	ContactPersonName  string `gorm:"size:255" json:"contact_person_name"`
	ContactPersonPhone string `gorm:"size:32" json:"contact_person_phone"`
	ContactTime        string `gorm:"size:32" json:"contact_time"`

	// Предварительный маршрут по утверждённой матрице: подсказка координатору.
	// Решение о маршруте принимает координатор, уровень нуждаемости — эксперт
	RouteHint          string `gorm:"size:8" json:"route_hint"`
	RouteRuleID        string `gorm:"size:32" json:"route_rule_id"`
	RouteReason        string `gorm:"size:1000" json:"route_reason"`
	RoutePolicyVersion string `gorm:"size:32" json:"route_policy_version"`
	RouteNote          string `gorm:"size:512" json:"route_note"`

	// Версия профиля собираемых данных, по которому заполнена карточка
	DataPolicyVersion string `gorm:"size:32" json:"data_policy_version"`

	// Владелец следующего действия — обязательное поле карточки
	NextActionOwner string     `gorm:"size:128" json:"next_action_owner"`
	NextActionDue   *time.Time `json:"next_action_due"`

	// Повторное обращение после ранее закрытого случая.
	// Repeat ставится, только если совпали заявитель и подопечный;
	// PossibleRepeat — если совпадений недостаточно и это решает координатор
	Repeat         bool   `json:"repeat"`
	PossibleRepeat bool   `json:"possible_repeat"`
	PreviousCase   string `gorm:"size:32" json:"previous_case"`

	SubmittedAt *time.Time `json:"submitted_at"`
	CreatedAt   time.Time  `json:"created_at"`
	UpdatedAt   time.Time  `json:"updated_at"`
}

// TableName задаёт имя таблицы обращений
func (Application) TableName() string {
	return "maxbot_applications"
}

// Event запись журнала по обращению: основа восстановимости истории случая
type Event struct {
	ID            uint      `gorm:"primaryKey" json:"id"`
	ApplicationID uint      `gorm:"index" json:"application_id"`
	Type          string    `gorm:"size:32" json:"type"`
	Details       string    `gorm:"size:2000" json:"details"`
	CreatedAt     time.Time `json:"created_at"`
}

// TableName задаёт имя таблицы журнала
func (Event) TableName() string {
	return "maxbot_events"
}

// Типы действий по случаю
const (
	// ActionInitialContact — координатор связывается с заявителем
	ActionInitialContact = "initial_contact"
	// ActionFollowUp7d — контроль на 7-й день: началась ли помощь
	ActionFollowUp7d = "follow_up_7d"
	// ActionFollowUp30d — контроль на 30-й день: продолжается ли помощь
	ActionFollowUp30d = "follow_up_30d"
	// ActionAfterEmergency — возврат к вопросу об уходе после STOP-1
	ActionAfterEmergency = "follow_up_after_emergency"
)

// Статусы действия
const (
	ActionStatusOpen      = "open"
	ActionStatusDone      = "done"
	ActionStatusCancelled = "cancelled"
)

// ActionTitles человекочитаемые названия действий
var ActionTitles = map[string]string{
	ActionInitialContact: "связаться с заявителем и оформить согласие на передачу",
	ActionFollowUp7d:     "контроль 7-го дня: началась ли помощь",
	ActionFollowUp30d:    "контроль 30-го дня: продолжается ли помощь",
	ActionAfterEmergency: "вернуться к вопросу об уходе после экстренной ситуации",
}

// Action следующее действие по случаю: кто, что и до какого срока должен сделать.
//
// Действия хранятся в базе, поэтому контрольные точки 7-го и 30-го дня
// переживают перезапуск приложения.
type Action struct {
	ID            uint       `gorm:"primaryKey" json:"id"`
	ApplicationID uint       `gorm:"index" json:"application_id"`
	PublicID      string     `gorm:"size:32;index" json:"public_id"`
	Type          string     `gorm:"size:32;index" json:"type"`
	Description   string     `gorm:"size:512" json:"description"`
	Owner         string     `gorm:"size:128" json:"owner"`
	DueAt         time.Time  `gorm:"index" json:"due_at"`
	Status        string     `gorm:"size:16;index" json:"status"`
	Result        string     `gorm:"size:1000" json:"result"`
	RemindedAt    *time.Time `json:"reminded_at"`
	CompletedAt   *time.Time `json:"completed_at"`
	CompletedBy   int64      `json:"completed_by"`
	CreatedAt     time.Time  `json:"created_at"`
	UpdatedAt     time.Time  `json:"updated_at"`
}

// TableName задаёт имя таблицы действий
func (Action) TableName() string {
	return "maxbot_actions"
}

// Title возвращает название действия
func (a *Action) Title() string {
	if title, ok := ActionTitles[a.Type]; ok {
		return title
	}

	return a.Type
}

// ProcessedEvent отметка об обработанном обновлении MAX.
//
// Мессенджер может доставить одно и то же событие повторно, а заявитель —
// нажать кнопку дважды: отметка защищает от повторной обработки.
type ProcessedEvent struct {
	Key       string    `gorm:"primaryKey;size:128" json:"key"`
	CreatedAt time.Time `json:"created_at"`
}

// TableName задаёт имя таблицы обработанных событий
func (ProcessedEvent) TableName() string {
	return "maxbot_processed_events"
}

// DailySequence дневной счётчик номеров обращений
type DailySequence struct {
	Day        string `gorm:"primaryKey;size:8" json:"day"`
	NextNumber int    `json:"next_number"`
}

// TableName задаёт имя таблицы счётчиков
func (DailySequence) TableName() string {
	return "maxbot_daily_sequences"
}

// SpheresList возвращает выбранные дополнительные сферы потребности
func (a *Application) SpheresList() []string {
	if strings.TrimSpace(a.OtherSpheres) == "" {
		return nil
	}

	parts := strings.Split(a.OtherSpheres, ",")
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			result = append(result, p)
		}
	}

	return result
}

// HasSphere сообщает, выбрана ли сфера потребности
func (a *Application) HasSphere(sphere string) bool {
	for _, s := range a.SpheresList() {
		if s == sphere {
			return true
		}
	}

	return false
}

// ToggleSphere добавляет или убирает сферу потребности
func (a *Application) ToggleSphere(sphere string) {
	current := a.SpheresList()
	result := make([]string, 0, len(current)+1)
	found := false
	for _, s := range current {
		if s == sphere {
			found = true
			continue
		}
		result = append(result, s)
	}
	if !found {
		result = append(result, sphere)
	}

	a.OtherSpheres = strings.Join(result, ",")
}
