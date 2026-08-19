package config

import (
	"os"
	"path/filepath"
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
stop1:
  approved: true
  signs:
    - id: s1
      question: "Человек без сознания?"
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
	if len(cfg.Stop1.Signs) != 1 || !cfg.Stop1.Approved {
		t.Fatalf("перечень STOP-1 прочитан неверно: %+v", cfg.Stop1)
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
		"пустой утверждённый перечень": func() *Config {
			c := &Config{}
			c.Bot.Token = "t"
			c.Bot.Mode = "polling"
			c.Stop1.Approved = true

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

func TestNextStepDeadline(t *testing.T) {
	cfg := &Config{}
	cfg.Intake.NextStepHours = 24

	from := time.Date(2026, 8, 19, 10, 0, 0, 0, time.UTC)
	if got := cfg.NextStepDeadline(from); !got.Equal(from.Add(24 * time.Hour)) {
		t.Fatalf("срок следующего действия рассчитан неверно: %v", got)
	}
}
