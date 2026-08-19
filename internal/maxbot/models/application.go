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

	// Фильтр STOP-1 / STOP-2 (обязательная отметка по каждому обращению)
	Stop1Index int        `json:"stop1_index"`
	Stop1Mark  string     `gorm:"size:16" json:"stop1_mark"`
	Stop1Sign  string     `gorm:"size:512" json:"stop1_sign"`
	Stop1At    *time.Time `json:"stop1_at"`

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

	// Обратная связь
	ContactPerson string `gorm:"size:255" json:"contact_person"`
	ContactTime   string `gorm:"size:32" json:"contact_time"`

	// Предварительный маршрут: рассчитывается ботом, решение принимает координатор
	RouteHint   string `gorm:"size:8" json:"route_hint"`
	RouteReason string `gorm:"size:1000" json:"route_reason"`

	// Владелец следующего действия — обязательное поле карточки
	NextActionOwner string     `gorm:"size:128" json:"next_action_owner"`
	NextActionDue   *time.Time `json:"next_action_due"`

	// Повторное обращение после ранее закрытого случая
	Repeat bool `json:"repeat"`

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
