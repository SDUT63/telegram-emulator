package survey

import (
	"strings"
	"testing"

	"telegram-emulator/internal/maxbot/models"
	"telegram-emulator/internal/maxbot/policy"
)

// testMatrix повторяет матрицу из configs/policies.yaml
func testMatrix() policy.Routing {
	return policy.Routing{
		Version:  "test-1",
		Approved: true,
		Rules: []policy.RoutingRule{
			{ID: "R010", Priority: 100, Conditions: map[string]string{"answers_complete": "false"},
				Route: "М4", Reason: "картина неясна"},
			{ID: "R020", Priority: 90, Conditions: map[string]string{"additional_spheres": ">0", "limited_count": ">0"},
				Route: "М5", Reason: "две и более сферы"},
			{ID: "R021", Priority: 90, Conditions: map[string]string{"additional_spheres": ">0", "medical_need": "yes"},
				Route: "М5", Reason: "две и более сферы"},
			{ID: "R030", Priority: 80, Conditions: map[string]string{"medical_need": "yes"},
				Route: "М1", Reason: "медицинская составляющая"},
			{ID: "R040", Priority: 70, Conditions: map[string]string{"lost_count": ">=3"},
				Route: "М3", Reason: "утрата по трём позициям"},
			{ID: "R050", Priority: 60, Conditions: map[string]string{"limited_count": ">0", "caregiver": "daily, sometimes"},
				Route: "М2", Reason: "частичная утрата, ухаживающий есть"},
			{ID: "R060", Priority: 50, Conditions: map[string]string{"limited_count": ">0", "caregiver": "none"},
				Route: "М4", Reason: "утрата без ухаживающего"},
			{ID: "R070", Priority: 10, Conditions: map[string]string{"limited_count": "=0", "medical_need": "no"},
				Route: "М4", Reason: "ограничений не зафиксировано"},
		},
	}
}

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
			expected: "М1",
		},
		{
			name: "частичная утрата и ухаживающий — М2",
			app: &models.Application{
				Mobility: AbilityHelp, Hygiene: AbilityHelp, Food: AbilitySelf,
				Caregiver: CaregiverDaily, MedicalNeed: AnswerNo,
			},
			expected: "М2",
		},
		{
			name: "утрата по трём позициям — М3",
			app: &models.Application{
				Mobility: AbilityCannot, Hygiene: AbilityCannot, Food: AbilityCannot,
				Caregiver: CaregiverSometimes, MedicalNeed: AnswerNo,
			},
			expected: "М3",
		},
		{
			name: "заявитель не знает ответов — М4",
			app: &models.Application{
				Mobility: AbilityUnknown, Hygiene: AbilityHelp, Food: AbilitySelf,
				Caregiver: CaregiverDaily, MedicalNeed: AnswerNo,
			},
			expected: "М4",
		},
		{
			name: "утрата без ухаживающего — М4",
			app: &models.Application{
				Mobility: AbilityHelp, Hygiene: AbilitySelf, Food: AbilitySelf,
				Caregiver: CaregiverNone, MedicalNeed: AnswerNo,
			},
			expected: "М4",
		},
		{
			name: "две и более сферы — М5",
			app: &models.Application{
				Mobility: AbilityHelp, Hygiene: AbilityHelp, Food: AbilitySelf,
				Caregiver: CaregiverDaily, MedicalNeed: AnswerNo,
				OtherSpheres: SphereDocuments + "," + SphereTransport,
			},
			expected: "М5",
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			decision := Route(c.app, testMatrix())
			if decision.Route != c.expected {
				t.Fatalf("ожидался маршрут %s, получен %s (%s)", c.expected, decision.Route, decision.Reason)
			}
			if decision.RuleID == "" {
				t.Fatal("решение должно ссылаться на правило матрицы")
			}
			if decision.PolicyVersion != "test-1" {
				t.Fatalf("решение должно фиксировать версию матрицы, получено %q", decision.PolicyVersion)
			}
		})
	}
}

func TestRouteNotAppliedWhenMatrixIsNotApproved(t *testing.T) {
	matrix := testMatrix()
	matrix.Approved = false

	decision := Route(&models.Application{Mobility: AbilityCannot, MedicalNeed: AnswerYes}, matrix)

	if decision.Route != "" || decision.Applied {
		t.Fatalf("неутверждённая матрица не должна назначать маршрут: %+v", decision)
	}
	if decision.Note == "" {
		t.Fatal("координатору нужно объяснить, почему маршрут не рассчитан")
	}
}

func TestFactsSummaryIsAlwaysAvailable(t *testing.T) {
	app := &models.Application{
		Mobility: AbilityCannot, Hygiene: AbilityHelp, Food: AbilityUnknown,
		Caregiver: CaregiverNone, MedicalNeed: AnswerNo,
	}

	summary := FactsSummary(app)
	for _, part := range []string{"не может — 1 из 3", "только с помощью — 1 из 3", "не знаю — 1 из 3"} {
		if !strings.Contains(summary, part) {
			t.Fatalf("в признаках случая нет части %q: %s", part, summary)
		}
	}
}

func TestViewerCardHidesContacts(t *testing.T) {
	app := &models.Application{
		PublicID: "MAX-20260819-0001", Stop1Mark: Stop2Mark,
		ApplicantName: "Иванова Мария", ApplicantPhone: "+79001234567",
		Address: "ул. Ленина, 10", Issue: "Отец не встаёт после больницы",
		District: "Автозаводский район", WardAge: 82,
	}

	card := CardForViewer(app)
	for _, secret := range []string{app.ApplicantPhone, app.Address, app.Issue, app.ApplicantName} {
		if strings.Contains(card, secret) {
			t.Fatalf("карточка для роли viewer не должна содержать %q", secret)
		}
	}
	if !strings.Contains(card, app.PublicID) {
		t.Fatal("карточка должна называть номер обращения")
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
		RouteHint: "М3", RouteRuleID: "R040", RoutePolicyVersion: "test-1",
		NextActionOwner: "координатор", Stop1PolicyVersion: "test-1", DataPolicyVersion: "DC-2026-01",
	}

	card := Card(app)
	for _, required := range []string{app.PublicID, app.ApplicantPhone, "STOP-2", "Владелец следующего действия"} {
		if !strings.Contains(card, required) {
			t.Fatalf("в карточке нет обязательной части %q", required)
		}
	}
	if !strings.Contains(card, "Профиль собираемых данных: DC-2026-01") {
		t.Fatal("карточка должна ссылаться на версию профиля собираемых данных")
	}
	if !strings.Contains(card, "Правило матрицы: R040") {
		t.Fatal("карточка должна называть сработавшее правило матрицы")
	}
}
