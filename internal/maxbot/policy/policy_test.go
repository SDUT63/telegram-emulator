package policy

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestStop1ValidationRequiresApprovalAttributes(t *testing.T) {
	cases := map[string]Stop1{
		"пустой утверждённый перечень": {Approved: true, Version: "1"},
		"без версии": {Approved: true, Signs: []Stop1Sign{
			{ID: "s1", Question: "?", Trigger: "yes", Action: ActionStop1}}},
		"без утвердившего": {Approved: true, Version: "1", Signs: []Stop1Sign{
			{ID: "s1", Question: "?", Trigger: "yes", Action: ActionStop1}}},
		"повтор идентификатора": {Approved: true, Version: "1", ApprovedBy: "врач", ApprovedAt: "2026-08-01",
			Signs: []Stop1Sign{
				{ID: "s1", Question: "?", Trigger: "yes", Action: ActionStop1},
				{ID: "s1", Question: "?", Trigger: "yes", Action: ActionStop1},
			}},
		"неизвестный trigger": {Approved: true, Version: "1", ApprovedBy: "врач", ApprovedAt: "2026-08-01",
			Signs: []Stop1Sign{{ID: "s1", Question: "?", Trigger: "maybe", Action: ActionStop1}}},
		"неизвестное действие": {Approved: true, Version: "1", ApprovedBy: "врач", ApprovedAt: "2026-08-01",
			Signs: []Stop1Sign{{ID: "s1", Question: "?", Trigger: "yes", Action: "escalate"}}},
	}

	for name, stop1 := range cases {
		t.Run(name, func(t *testing.T) {
			if err := stop1.Validate(); err == nil {
				t.Fatal("ожидалась ошибка проверки перечня")
			}
		})
	}

	// Неутверждённый перечень не проверяется: его всё равно нельзя применить
	draft := Stop1{Signs: []Stop1Sign{{ID: "", Question: ""}}}
	if err := draft.Validate(); err != nil {
		t.Fatalf("черновик перечня не должен приводить к ошибке: %v", err)
	}
}

func TestStop1SignTrigger(t *testing.T) {
	sign := Stop1Sign{ID: "s1", Trigger: "yes", Action: ActionStop1}

	if !sign.Triggered("yes") || sign.Triggered("no") {
		t.Fatal("признак срабатывает на ответ, указанный в перечне")
	}

	inverted := Stop1Sign{ID: "n1", Trigger: "no", Action: ActionStop1}
	if !inverted.Triggered("no") || inverted.Triggered("yes") {
		t.Fatal("признак с trigger no срабатывает на отрицательный ответ")
	}
}

func TestRoutingPriorityDecidesOrder(t *testing.T) {
	matrix := Routing{
		Version:  "1",
		Approved: true,
		Rules: []RoutingRule{
			{ID: "low", Priority: 10, Conditions: map[string]string{"medical_need": "yes"}, Route: "М1"},
			{ID: "high", Priority: 100, Conditions: map[string]string{"medical_need": "yes"}, Route: "М5"},
		},
	}

	decision := matrix.Apply(Facts{MedicalNeed: "yes", MedicalKnown: true, CaregiverKnown: true})
	if decision.RuleID != "high" || decision.Route != "М5" {
		t.Fatalf("должно срабатывать правило с большим приоритетом: %+v", decision)
	}
}

func TestRoutingConditions(t *testing.T) {
	matrix := Routing{
		Version:  "1",
		Approved: true,
		Rules: []RoutingRule{
			{ID: "R1", Priority: 10, Route: "М3", Conditions: map[string]string{
				"lost_count": ">=3", "caregiver": "none, sometimes",
			}},
		},
	}

	match := matrix.Apply(Facts{LostCount: 3, Caregiver: "none", MedicalKnown: true, CaregiverKnown: true})
	if match.RuleID != "R1" {
		t.Fatalf("правило должно сработать: %+v", match)
	}

	// Не выполнено одно из условий — правило не применяется
	miss := matrix.Apply(Facts{LostCount: 2, Caregiver: "none", MedicalKnown: true, CaregiverKnown: true})
	if miss.RuleID != "" || miss.Route != "" {
		t.Fatalf("правило не должно срабатывать: %+v", miss)
	}
	if miss.Note == "" {
		t.Fatal("нужно объяснить, почему маршрут не определён")
	}
}

func TestRoutingNotAppliedUntilApproved(t *testing.T) {
	matrix := Routing{Version: "1", Rules: []RoutingRule{
		{ID: "R1", Priority: 10, Route: "М1", Conditions: map[string]string{"medical_need": "yes"}},
	}}

	decision := matrix.Apply(Facts{MedicalNeed: "yes"})
	if decision.Applied || decision.Route != "" {
		t.Fatalf("неутверждённая матрица не применяется: %+v", decision)
	}
}

func TestRoutingValidationRejectsUnknownConditions(t *testing.T) {
	matrix := Routing{Version: "1", Approved: true, Rules: []RoutingRule{
		{ID: "R1", Route: "М1", Conditions: map[string]string{"blood_pressure": ">140"}},
	}}

	if err := matrix.Validate(); err == nil {
		t.Fatal("неизвестное условие должно отклоняться при загрузке правил")
	}
}

func TestLoadProjectPolicies(t *testing.T) {
	set, err := Load(filepath.Join("..", "..", "..", "configs", "policies.yaml"))
	if err != nil {
		t.Fatalf("файл правил проекта должен загружаться: %v", err)
	}

	if set.Stop1.Approved {
		t.Fatal("черновой перечень STOP-1 не должен быть отмечен как утверждённый")
	}
	if set.Routing.Approved {
		t.Fatal("черновая матрица маршрутов не должна быть отмечена как утверждённая")
	}
	if len(set.Stop1.Signs) == 0 || len(set.Routing.Rules) == 0 {
		t.Fatal("в файле правил должны быть заготовки перечня и матрицы")
	}
	if set.DataCollection.Version == "" {
		t.Fatal("профиль собираемых данных должен иметь версию")
	}

	// Проверяем, что заготовки станут корректными после утверждения
	set.Stop1.Approved = true
	set.Stop1.ApprovedBy = "заведующий отделением ПМП"
	set.Stop1.ApprovedAt = "2026-09-01"
	set.Stop1.Version = "2026-09-01"
	set.Routing.Approved = true
	set.Routing.Version = "2026-09-01"

	if err := set.Validate(); err != nil {
		t.Fatalf("после утверждения правила должны проходить проверку: %v", err)
	}

	for _, excluded := range set.DataCollection.Excluded {
		if strings.TrimSpace(excluded) == "" {
			t.Fatal("список несобираемых данных не должен содержать пустых строк")
		}
	}
}
