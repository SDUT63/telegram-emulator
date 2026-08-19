// Package config содержит конфигурацию чат-бота «Точка входа» для мессенджера MAX.
package config

import (
	"fmt"
	"strings"
	"time"

	"github.com/spf13/viper"
)

// Config представляет конфигурацию чат-бота
type Config struct {
	Bot          BotConfig          `mapstructure:"bot"`
	Database     DatabaseConfig     `mapstructure:"database"`
	Organization OrganizationConfig `mapstructure:"organization"`
	Coordinator  CoordinatorConfig  `mapstructure:"coordinator"`
	Intake       IntakeConfig       `mapstructure:"intake"`
	Stop1        Stop1Config        `mapstructure:"stop1"`
	Logging      LoggingConfig      `mapstructure:"logging"`
}

// BotConfig параметры подключения к MAX Bot API
type BotConfig struct {
	Token   string        `mapstructure:"token"`
	APIURL  string        `mapstructure:"api_url"`
	Mode    string        `mapstructure:"mode"` // polling | webhook
	Timeout string        `mapstructure:"timeout"`
	Debug   bool          `mapstructure:"debug"`
	Webhook WebhookConfig `mapstructure:"webhook"`
}

// WebhookConfig параметры режима webhook
type WebhookConfig struct {
	URL    string `mapstructure:"url"`
	Listen string `mapstructure:"listen"`
	Path   string `mapstructure:"path"`
	Secret string `mapstructure:"secret"`
}

// DatabaseConfig конфигурация базы данных обращений
type DatabaseConfig struct {
	URL string `mapstructure:"url"`
}

// OrganizationConfig сведения о службе, которые бот называет заявителю
type OrganizationConfig struct {
	Name        string `mapstructure:"name"`
	ServiceName string `mapstructure:"service_name"`
	Phone       string `mapstructure:"phone"`
	WorkHours   string `mapstructure:"work_hours"`
	PublicURL   string `mapstructure:"public_url"`
	PolicyURL   string `mapstructure:"policy_url"`
}

// CoordinatorConfig получатели карточек обращений внутри MAX
type CoordinatorConfig struct {
	ChatID  int64   `mapstructure:"chat_id"`
	UserIDs []int64 `mapstructure:"user_ids"`
}

// IntakeConfig параметры анкеты первичного обращения
type IntakeConfig struct {
	Districts     []string `mapstructure:"districts"`
	NextStepHours int      `mapstructure:"next_step_hours"`
}

// Stop1Config закрытый перечень признаков экстренного состояния.
// Перечень разрабатывает и подписывает медицинский специалист;
// до получения подписанного перечня линия не открывается.
type Stop1Config struct {
	Approved   bool        `mapstructure:"approved"`
	ApprovedBy string      `mapstructure:"approved_by"`
	ApprovedAt string      `mapstructure:"approved_at"`
	Signs      []Stop1Sign `mapstructure:"signs"`
}

// Stop1Sign один признак из перечня STOP-1
type Stop1Sign struct {
	ID       string `mapstructure:"id"`
	Question string `mapstructure:"question"`
}

// LoggingConfig конфигурация логирования
type LoggingConfig struct {
	Level  string `mapstructure:"level"`
	Format string `mapstructure:"format"`
	File   string `mapstructure:"file"`
}

// Load загружает конфигурацию из файла и переменных окружения
func Load() (*Config, error) {
	v := viper.New()
	v.SetConfigName("maxbot")
	v.SetConfigType("yaml")
	v.AddConfigPath("./configs")
	v.AddConfigPath(".")

	v.SetEnvPrefix("MAXBOT")
	v.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))
	v.AutomaticEnv()

	setDefaults(v)

	// Токен всегда читается из окружения, даже если файла конфигурации нет
	if err := v.BindEnv("bot.token", "MAXBOT_BOT_TOKEN"); err != nil {
		return nil, fmt.Errorf("ошибка привязки переменной окружения: %w", err)
	}

	if err := v.ReadInConfig(); err != nil {
		if _, ok := err.(viper.ConfigFileNotFoundError); !ok {
			return nil, fmt.Errorf("ошибка чтения конфигурационного файла: %w", err)
		}
	}

	var cfg Config
	if err := v.Unmarshal(&cfg); err != nil {
		return nil, fmt.Errorf("ошибка парсинга конфигурации: %w", err)
	}

	return &cfg, nil
}

// setDefaults устанавливает значения по умолчанию
func setDefaults(v *viper.Viper) {
	v.SetDefault("bot.token", "")
	v.SetDefault("bot.api_url", "")
	v.SetDefault("bot.mode", "polling")
	v.SetDefault("bot.timeout", "30s")
	v.SetDefault("bot.debug", false)
	v.SetDefault("bot.webhook.listen", ":10888")
	v.SetDefault("bot.webhook.path", "/webhook")

	v.SetDefault("database.url", "sqlite://data/maxbot.db")

	v.SetDefault("organization.name", "АНО «Система долговременного ухода Тольятти»")
	v.SetDefault("organization.service_name", "Точка входа")
	v.SetDefault("organization.work_hours", "по будням с 9:00 до 18:00")

	v.SetDefault("intake.next_step_hours", 24)
	v.SetDefault("intake.districts", []string{
		"Автозаводский район",
		"Центральный район",
		"Комсомольский район",
	})

	v.SetDefault("stop1.approved", false)

	v.SetDefault("logging.level", "info")
	v.SetDefault("logging.format", "console")
	v.SetDefault("logging.file", "")
}

// Validate проверяет, что конфигурации достаточно для запуска бота
func (c *Config) Validate() error {
	if strings.TrimSpace(c.Bot.Token) == "" {
		return fmt.Errorf("не задан токен бота: укажите bot.token или переменную окружения MAXBOT_BOT_TOKEN")
	}

	switch c.Bot.Mode {
	case "polling":
	case "webhook":
		if strings.TrimSpace(c.Bot.Webhook.URL) == "" {
			return fmt.Errorf("для режима webhook требуется bot.webhook.url")
		}
	default:
		return fmt.Errorf("неизвестный режим работы бота: %q (допустимо polling или webhook)", c.Bot.Mode)
	}

	if c.Stop1.Approved && len(c.Stop1.Signs) == 0 {
		return fmt.Errorf("перечень STOP-1 отмечен как утверждённый, но пуст")
	}

	return nil
}

// GetTimeout возвращает таймаут запросов к MAX Bot API
func (c *Config) GetTimeout() (time.Duration, error) {
	if strings.TrimSpace(c.Bot.Timeout) == "" {
		return 30 * time.Second, nil
	}

	return time.ParseDuration(c.Bot.Timeout)
}

// NextStepDeadline возвращает срок следующего действия по обращению
func (c *Config) NextStepDeadline(from time.Time) time.Time {
	hours := c.Intake.NextStepHours
	if hours <= 0 {
		hours = 24
	}

	return from.Add(time.Duration(hours) * time.Hour)
}

// IsCoordinator сообщает, входит ли пользователь в список координаторов
func (c *Config) IsCoordinator(userID int64) bool {
	for _, id := range c.Coordinator.UserIDs {
		if id == userID {
			return true
		}
	}

	return false
}
