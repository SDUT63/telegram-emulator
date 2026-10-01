package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLoadFromFileAndEnv(t *testing.T) {
	dir := t.TempDir()
	content := `
bot:
  mode: polling
organization:
  name: "АНО «СДУТ»"
coordinator:
  chat_id: -100
  user_ids: [11, 22]
intake:
  districts: ["Автозаводский район"]
working_calendar:
  timezone: "Europe/Samara"
  days: ["mon", "tue", "wed", "thu", "fri"]
  start: "09:00"
  end: "18:00"
access:
  viewers: [33]
  supervisors: [44]
`
	if err := os.WriteFile(filepath.Join(dir, "maxbot.yaml"), []byte(content), 0o600); err != nil {
		t.Fatalf("не удалось записать конфигурацию: %v", err)
	}

	t.Chdir(dir)
	t.Setenv("MAXBOT_BOT_TOKEN", "test-token")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("ошибка загрузки конфигурации: %v", err)
	}

	if cfg.Bot.Token != "test-token" {
		t.Fatalf("токен должен читаться из окружения, получено %q", cfg.Bot.Token)
	}
	if cfg.Coordinator.ChatID != -100 || len(cfg.Coordinator.UserIDs) != 2 {
		t.Fatalf("получатели карточек прочитаны неверно: %+v", cfg.Coordinator)
	}
	if !cfg.IsCoordinator(22) || cfg.IsCoordinator(33) {
		t.Fatal("проверка координатора работает неверно")
	}
	if cfg.Role(22) != RoleCoordinator || cfg.Role(33) != RoleViewer || cfg.Role(44) != RoleSupervisor {
		t.Fatalf("роли распределены неверно: %+v", cfg.Access)
	}
	if cfg.Role(99) != RoleNone {
		t.Fatal("посторонний пользователь не должен получать роль")
	}
	if !CanSeeFullCard(RoleCoordinator) || CanSeeFullCard(RoleViewer) {
		t.Fatal("полную карточку видит координатор, но не viewer")
	}
	if !CanSeeCases(RoleViewer) || CanCompleteActions(RoleViewer) {
		t.Fatal("viewer видит случаи, но не закрывает действия")
	}
	if strings.TrimSpace(cfg.Consent.Text) == "" {
		t.Fatal("должна применяться формулировка согласия по умолчанию")
	}
	if strings.Contains(cfg.Consent.Text, "запись будет удалена") {
		t.Fatal("бот не должен обещать удаление записи при отзыве согласия")
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("конфигурация должна быть корректной: %v", err)
	}

	timeout, err := cfg.GetTimeout()
	if err != nil || timeout != 30*time.Second {
		t.Fatalf("таймаут по умолчанию 30s, получено %v (%v)", timeout, err)
	}
}

func TestValidate(t *testing.T) {
	cases := map[string]*Config{
		"без токена": func() *Config {
			c := &Config{}
			c.Bot.Mode = "polling"

			return c
		}(),
		"неизвестный режим": func() *Config {
			c := &Config{}
			c.Bot.Token = "t"
			c.Bot.Mode = "carrier-pigeon"

			return c
		}(),
		"webhook без адреса": func() *Config {
			c := &Config{}
			c.Bot.Token = "t"
			c.Bot.Mode = "webhook"

			return c
		}(),
		"некорректный рабочий календарь": func() *Config {
			c := &Config{}
			c.Bot.Token = "t"
			c.Bot.Mode = "polling"
			c.Policies.Path = "configs/policies.yaml"
			c.Consent.Text = "текст"
			c.Calendar.Timezone = "Mars/Olympus"

			return c
		}(),
		"без формулировки согласия": func() *Config {
			c := &Config{}
			c.Bot.Token = "t"
			c.Bot.Mode = "polling"
			c.Policies.Path = "configs/policies.yaml"

			return c
		}(),
	}

	for name, cfg := range cases {
		t.Run(name, func(t *testing.T) {
			if err := cfg.Validate(); err == nil {
				t.Fatal("ожидалась ошибка конфигурации")
			}
		})
	}
}

func TestWorkingCalendarFromConfig(t *testing.T) {
	cfg := &Config{}
	cfg.Calendar.Timezone = "Europe/Samara"
	cfg.Calendar.Days = []string{"mon", "tue", "wed", "thu", "fri"}
	cfg.Calendar.Start = "09:00"
	cfg.Calendar.End = "18:00"

	workingCalendar, err := cfg.WorkingCalendar()
	if err != nil {
		t.Fatalf("календарь не собрался: %v", err)
	}

	friday := time.Date(2026, 8, 14, 17, 55, 0, 0, workingCalendar.Location())
	deadline := workingCalendar.NextWorkingDeadline(friday, 1)

	if !workingCalendar.IsWorkingDay(deadline) {
		t.Fatalf("срок должен приходиться на рабочий день: %s", deadline.Format("2006-01-02 15:04"))
	}
	if deadline.Before(friday.Add(24 * time.Hour)) {
		t.Fatal("срок в один рабочий день с вечера пятницы не может истечь в субботу")
	}
}
