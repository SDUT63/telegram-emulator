// Package policy хранит утверждаемые правила службы «Точка входа»:
// перечень признаков STOP-1, матрицу маршрутов и профиль собираемых данных.
//
// Правила задаются файлами конфигурации и версионируются: код их исполняет,
// но не содержит собственных медицинских, социальных и юридических решений.
// Пока правило не отмечено как утверждённое, оно не применяется.
package policy

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// Действия по признаку STOP-1
const (
	// ActionStop1 — обычная навигация прекращается, выполняется протокол STOP-1
	ActionStop1 = "STOP-1"
)

// Stop1 закрытый перечень признаков возможного экстренного состояния
type Stop1 struct {
	Version    string      `yaml:"version"`
	Approved   bool        `yaml:"approved"`
	ApprovedBy string      `yaml:"approved_by"`
	ApprovedAt string      `yaml:"approved_at"`
	Signs      []Stop1Sign `yaml:"signs"`
}

// Stop1Sign один признак перечня
type Stop1Sign struct {
	ID       string `yaml:"id"`
	Question string `yaml:"question"`
	// Trigger — ответ, при котором выполняется действие: yes либо no
	Trigger string `yaml:"trigger"`
	// Action — действие при срабатывании признака
	Action string `yaml:"action"`
}

// Validate проверяет перечень STOP-1
func (p *Stop1) Validate() error {
	if !p.Approved {
		return nil
	}

	if len(p.Signs) == 0 {
		return fmt.Errorf("перечень STOP-1 отмечен как утверждённый, но пуст")
	}
	if strings.TrimSpace(p.Version) == "" {
		return fmt.Errorf("у утверждённого перечня STOP-1 должна быть версия")
	}
	if strings.TrimSpace(p.ApprovedBy) == "" || strings.TrimSpace(p.ApprovedAt) == "" {
		return fmt.Errorf("у утверждённого перечня STOP-1 должны быть указаны approved_by и approved_at")
	}

	seen := make(map[string]bool, len(p.Signs))
	for i, sign := range p.Signs {
		if strings.TrimSpace(sign.ID) == "" {
			return fmt.Errorf("признак %d: не указан id", i+1)
		}
		if seen[sign.ID] {
			return fmt.Errorf("признак %s: идентификатор повторяется", sign.ID)
		}
		seen[sign.ID] = true

		if strings.TrimSpace(sign.Question) == "" {
			return fmt.Errorf("признак %s: не указана формулировка вопроса", sign.ID)
		}
		if sign.Trigger != "yes" && sign.Trigger != "no" {
			return fmt.Errorf("признак %s: trigger должен быть yes или no, получено %q", sign.ID, sign.Trigger)
		}
		if sign.Action != ActionStop1 {
			return fmt.Errorf("признак %s: неизвестное действие %q (поддерживается %s)", sign.ID, sign.Action, ActionStop1)
		}
	}

	return nil
}

// Triggered сообщает, означает ли ответ срабатывание признака
func (s Stop1Sign) Triggered(answer string) bool {
	return answer == s.Trigger
}

// Routing матрица маршрутов: правила выбора предварительного направления
type Routing struct {
	Version    string        `yaml:"version"`
	Approved   bool          `yaml:"approved"`
	ApprovedBy string        `yaml:"approved_by"`
	ApprovedAt string        `yaml:"approved_at"`
	Rules      []RoutingRule `yaml:"rules"`
}

// RoutingRule одно правило матрицы
type RoutingRule struct {
	ID         string            `yaml:"id"`
	Priority   int               `yaml:"priority"`
	Conditions map[string]string `yaml:"conditions"`
	Route      string            `yaml:"route"`
	Reason     string            `yaml:"reason"`
}

// Facts признаки случая, к которым применяются правила матрицы
type Facts struct {
	// MedicalNeed, Caregiver — ответы анкеты как есть
	MedicalNeed string
	Caregiver   string
	// LostCount, PartialCount, UnknownCount — число позиций самообслуживания
	LostCount    int
	PartialCount int
	UnknownCount int
	// AdditionalSpheres — число отмеченных дополнительных сфер потребности
	AdditionalSpheres int
	// CaregiverKnown сообщает, известен ли ответ об ухаживающем
	CaregiverKnown bool
	// MedicalKnown сообщает, известен ли ответ о медицинской составляющей
	MedicalKnown bool
}

// Decision результат применения матрицы
type Decision struct {
	// Route — предварительное направление; пусто, если правило не найдено
	// либо матрица не утверждена
	Route string
	// RuleID — идентификатор сработавшего правила
	RuleID string
	// Reason — основание из правила
	Reason string
	// PolicyVersion — версия матрицы, по которой принято решение
	PolicyVersion string
	// Applied сообщает, применялась ли матрица
	Applied bool
	// Note — пояснение, почему матрица не применялась
	Note string
}

// Validate проверяет матрицу маршрутов
func (p *Routing) Validate() error {
	if !p.Approved {
		return nil
	}

	if len(p.Rules) == 0 {
		return fmt.Errorf("матрица маршрутов отмечена как утверждённая, но пуста")
	}
	if strings.TrimSpace(p.Version) == "" {
		return fmt.Errorf("у утверждённой матрицы маршрутов должна быть версия")
	}

	seen := make(map[string]bool, len(p.Rules))
	for i, rule := range p.Rules {
		if strings.TrimSpace(rule.ID) == "" {
			return fmt.Errorf("правило %d: не указан id", i+1)
		}
		if seen[rule.ID] {
			return fmt.Errorf("правило %s: идентификатор повторяется", rule.ID)
		}
		seen[rule.ID] = true

		if strings.TrimSpace(rule.Route) == "" {
			return fmt.Errorf("правило %s: не указан маршрут", rule.ID)
		}
		for key, condition := range rule.Conditions {
			if err := validateCondition(key, condition); err != nil {
				return fmt.Errorf("правило %s: %w", rule.ID, err)
			}
		}
	}

	return nil
}

// Apply применяет матрицу к признакам случая.
//
// Правила проверяются в порядке приоритета, срабатывает первое подходящее.
// Решение по маршруту в любом случае принимает координатор.
func (p *Routing) Apply(facts Facts) Decision {
	if !p.Approved {
		return Decision{
			Note: "матрица маршрутов не утверждена: предварительное направление не рассчитывается",
		}
	}

	rules := make([]RoutingRule, len(p.Rules))
	copy(rules, p.Rules)
	// Более высокий приоритет проверяется раньше
	for i := 1; i < len(rules); i++ {
		for j := i; j > 0 && rules[j].Priority > rules[j-1].Priority; j-- {
			rules[j], rules[j-1] = rules[j-1], rules[j]
		}
	}

	for _, rule := range rules {
		if matches(rule, facts) {
			return Decision{
				Route:         rule.Route,
				RuleID:        rule.ID,
				Reason:        rule.Reason,
				PolicyVersion: p.Version,
				Applied:       true,
			}
		}
	}

	return Decision{
		PolicyVersion: p.Version,
		Applied:       true,
		Note:          "ни одно правило матрицы не подошло: маршрут определяет координатор",
	}
}

// matches проверяет, подходит ли правило под признаки случая
func matches(rule RoutingRule, facts Facts) bool {
	for key, condition := range rule.Conditions {
		if !conditionHolds(key, condition, facts) {
			return false
		}
	}

	return len(rule.Conditions) > 0
}

// conditionHolds проверяет одно условие правила
func conditionHolds(key, condition string, facts Facts) bool {
	switch key {
	case "medical_need":
		return inList(condition, facts.MedicalNeed)
	case "caregiver":
		return inList(condition, facts.Caregiver)
	case "lost_count":
		return compare(condition, facts.LostCount)
	case "partial_count":
		return compare(condition, facts.PartialCount)
	case "limited_count":
		return compare(condition, facts.LostCount+facts.PartialCount)
	case "unknown_count":
		return compare(condition, facts.UnknownCount)
	case "additional_spheres":
		return compare(condition, facts.AdditionalSpheres)
	case "answers_complete":
		complete := facts.UnknownCount == 0 && facts.CaregiverKnown && facts.MedicalKnown

		return (condition == "true") == complete
	}

	return false
}

// validateCondition проверяет синтаксис условия
func validateCondition(key, condition string) error {
	switch key {
	case "medical_need", "caregiver":
		if strings.TrimSpace(condition) == "" {
			return fmt.Errorf("условие %s не может быть пустым", key)
		}

		return nil
	case "lost_count", "partial_count", "limited_count", "unknown_count", "additional_spheres":
		if _, _, err := parseComparison(condition); err != nil {
			return fmt.Errorf("условие %s: %w", key, err)
		}

		return nil
	case "answers_complete":
		if condition != "true" && condition != "false" {
			return fmt.Errorf("условие answers_complete должно быть true или false, получено %q", condition)
		}

		return nil
	}

	return fmt.Errorf("неизвестное условие %q", key)
}

// inList проверяет вхождение значения в список вида "yes, unknown"
func inList(condition, value string) bool {
	for _, item := range strings.Split(condition, ",") {
		if strings.TrimSpace(item) == value {
			return true
		}
	}

	return false
}

// compare проверяет числовое условие вида ">=3", ">0", "=0"
func compare(condition string, value int) bool {
	operator, threshold, err := parseComparison(condition)
	if err != nil {
		return false
	}

	switch operator {
	case ">":
		return value > threshold
	case ">=":
		return value >= threshold
	case "<":
		return value < threshold
	case "<=":
		return value <= threshold
	case "=":
		return value == threshold
	}

	return false
}

// parseComparison разбирает числовое условие
func parseComparison(condition string) (string, int, error) {
	condition = strings.TrimSpace(condition)
	for _, operator := range []string{">=", "<=", ">", "<", "="} {
		if strings.HasPrefix(condition, operator) {
			value, err := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(condition, operator)))
			if err != nil {
				return "", 0, fmt.Errorf("ожидается число после %q, получено %q", operator, condition)
			}

			return operator, value, nil
		}
	}

	value, err := strconv.Atoi(condition)
	if err != nil {
		return "", 0, fmt.Errorf("ожидается сравнение вида >=3 или число, получено %q", condition)
	}

	return "=", value, nil
}

// DataCollection профиль собираемых данных: что собирается и что не собирается
type DataCollection struct {
	Version  string   `yaml:"version"`
	Collect  []string `yaml:"collect"`
	Excluded []string `yaml:"excluded"`
}

// Set полный набор правил службы
type Set struct {
	Stop1          Stop1          `yaml:"stop1"`
	Routing        Routing        `yaml:"routing"`
	DataCollection DataCollection `yaml:"data_collection"`
}

// Load читает правила из файла
func Load(path string) (*Set, error) {
	data, err := os.ReadFile(path) // #nosec G304 -- путь задаёт администратор в конфигурации
	if err != nil {
		return nil, fmt.Errorf("ошибка чтения файла правил %s: %w", path, err)
	}

	var set Set
	if err := yaml.Unmarshal(data, &set); err != nil {
		return nil, fmt.Errorf("ошибка разбора файла правил %s: %w", path, err)
	}

	if err := set.Validate(); err != nil {
		return nil, fmt.Errorf("файл правил %s: %w", path, err)
	}

	return &set, nil
}

// Validate проверяет весь набор правил
func (s *Set) Validate() error {
	if err := s.Stop1.Validate(); err != nil {
		return err
	}

	return s.Routing.Validate()
}
