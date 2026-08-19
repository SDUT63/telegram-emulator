package survey

import (
	"strings"
	"testing"

	"telegram-emulator/internal/maxbot/models"
)

func TestRouteMatrix(t *testing.T) {
	cases := []struct {
		name     string
		app      *models.Application
		expected string
	}{
		{
			name: "медицинская составляющая — М1",
			app: &models.Application{
				Mobility: AbilityHelp, Hygiene: AbilityHelp, Food: AbilitySelf,
				Caregiver: CaregiverDaily, MedicalNeed: AnswerYes,
			},
			expected: RouteM1,
		},
		{
			name: "частичная утрата и ухаживающий — М2",
			app: &models.Application{
				Mobility: AbilityHelp, Hygiene: AbilityHelp, Food: AbilitySelf,
				Caregiver: CaregiverDaily, MedicalNeed: AnswerNo,
			},
			expected: RouteM2,
		},
		{
			name: "утрата по трём позициям — М3",
			app: &models.Application{
				Mobility: AbilityCannot, Hygiene: AbilityCannot, Food: AbilityCannot,
				Caregiver: CaregiverSometimes, MedicalNeed: AnswerNo,
			},
			expected: RouteM3,
		},
		{
			name: "заявитель не знает ответов — М4",
			app: &models.Application{
				Mobility: AbilityUnknown, Hygiene: AbilityHelp, Food: AbilitySelf,
				Caregiver: CaregiverDaily, MedicalNeed: AnswerNo,
			},
			expected: RouteM4,
		},
		{
			name: "утрата без ухаживающего — М4",
			app: &models.Application{
				Mobility: AbilityHelp, Hygiene: AbilitySelf, Food: AbilitySelf,
				Caregiver: CaregiverNone, MedicalNeed: AnswerNo,
			},
			expected: RouteM4,
		},
		{
			name: "две и более сферы — М5",
			app: &models.Application{
				Mobility: AbilityHelp, Hygiene: AbilityHelp, Food: AbilitySelf,
				Caregiver: CaregiverDaily, MedicalNeed: AnswerNo,
				OtherSpheres: SphereDocuments + "," + SphereTransport,
			},
			expected: RouteM5,
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			route, reason := Route(c.app)
			if route != c.expected {
				t.Fatalf("ожидался маршрут %s, получен %s (%s)", c.expected, route, reason)
			}
			if !strings.Contains(reason, "решение принимает координатор") {
				t.Fatalf("обоснование должно отмечать предварительность маршрута: %s", reason)
			}
		})
	}
}

func TestCardHidesDataWeDoNotCollect(t *testing.T) {
	app := &models.Application{
		PublicID: "MAX-20260819-0001", Channel: "max", Stop1Mark: "STOP-2",
		ApplicantName: "Иванова Мария", ApplicantPhone: "+79001234567",
		Relation: RelationRelative, WardName: "Иванов Пётр", WardAge: 82,
		District: "Автозаводский район", Address: "ул. Ленина, 10",
		Issue: "Отец не встаёт после больницы", ConsentPD: true,
		Mobility: AbilityCannot, Hygiene: AbilityCannot, Food: AbilityHelp,
		Caregiver: CaregiverSometimes, MedicalNeed: AnswerNo,
		RouteHint: RouteM3, NextActionOwner: "координатор",
	}

	card := Card(app)
	for _, required := range []string{app.PublicID, app.ApplicantPhone, "STOP-2", "Владелец следующего действия"} {
		if !strings.Contains(card, required) {
			t.Fatalf("в карточке нет обязательной части %q", required)
		}
	}
	if !strings.Contains(card, "Диагноз, сведения о доходах и имуществе не собирались") {
		t.Fatal("карточка должна фиксировать, что запрещённые поля не собирались")
	}
}
