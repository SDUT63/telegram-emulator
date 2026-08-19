// Команда maxbot запускает чат-бот службы «Точка входа» в мессенджере MAX:
// принимает обращение, собирает контактные сведения и передаёт карточку координатору.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"telegram-emulator/internal/maxbot/bot"
	"telegram-emulator/internal/maxbot/config"
	"telegram-emulator/internal/maxbot/storage"
	"telegram-emulator/internal/pkg/logger"

	maxbot "github.com/max-messenger/max-bot-api-client-go"
	"github.com/max-messenger/max-bot-api-client-go/schemes"
	"go.uber.org/zap"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("Ошибка загрузки конфигурации: %v", err)
	}

	if err := cfg.Validate(); err != nil {
		log.Fatalf("Ошибка конфигурации: %v", err)
	}

	if err := logger.Init(cfg.Logging.Level, cfg.Logging.Format, cfg.Logging.File); err != nil {
		log.Fatalf("Ошибка инициализации логгера: %v", err)
	}
	defer func() {
		if err := logger.Sync(); err != nil {
			log.Printf("Ошибка синхронизации логгера: %v", err)
		}
	}()

	appLog := logger.GetLogger()
	appLog.Info("Запуск чат-бота «Точка входа» для MAX...")

	if !cfg.Stop1.Approved || len(cfg.Stop1.Signs) == 0 {
		appLog.Warn("перечень STOP-1 не утверждён: бот работает в информационном режиме и не принимает обращения")
	}

	store, err := storage.Open(cfg.Database.URL)
	if err != nil {
		appLog.Fatal("Ошибка инициализации базы данных", zap.Error(err))
	}
	defer func() {
		if err := store.Close(); err != nil {
			appLog.Error("Ошибка закрытия базы данных", zap.Error(err))
		}
	}()

	timeout, err := cfg.GetTimeout()
	if err != nil {
		appLog.Fatal("Некорректный таймаут запросов", zap.Error(err))
	}

	options := []maxbot.Option{maxbot.WithApiTimeout(timeout)}
	if cfg.Bot.APIURL != "" {
		options = append(options, maxbot.WithBaseURL(cfg.Bot.APIURL))
	}
	if cfg.Bot.Debug {
		options = append(options, maxbot.WithDebugMode())
	}

	api, err := maxbot.New(cfg.Bot.Token, options...)
	if err != nil {
		appLog.Fatal("Ошибка подключения к MAX Bot API", zap.Error(err))
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	info, err := api.Bots.GetBot(ctx)
	if err != nil {
		appLog.Fatal("Не удалось получить сведения о боте: проверьте токен", zap.Error(err))
	}
	appLog.Info("Бот подключён", zap.String("name", info.Name), zap.String("username", info.Username))

	handler := bot.New(cfg, bot.NewOutbox(api.Messages), store, appLog)

	go func() {
		for err := range api.GetErrors() {
			appLog.Error("Ошибка MAX Bot API", zap.Error(err))
		}
	}()

	switch cfg.Bot.Mode {
	case "webhook":
		runWebhook(ctx, cfg, api, handler, appLog)
	default:
		runPolling(ctx, api, handler, appLog)
	}

	appLog.Info("Чат-бот остановлен")
}

// runPolling принимает обновления через long polling
func runPolling(ctx context.Context, api *maxbot.Api, handler *bot.Bot, appLog *zap.Logger) {
	appLog.Info("Режим получения обновлений: long polling")

	for update := range api.GetUpdates(ctx) {
		handler.HandleUpdate(ctx, update)
	}
}

// runWebhook принимает обновления через webhook
func runWebhook(ctx context.Context, cfg *config.Config, api *maxbot.Api, handler *bot.Bot, appLog *zap.Logger) {
	path := cfg.Bot.Webhook.Path
	if path == "" {
		path = "/webhook"
	}

	subscription, err := api.Subscriptions.Subscribe(ctx, cfg.Bot.Webhook.URL, []string{}, cfg.Bot.Webhook.Secret)
	if err != nil {
		appLog.Fatal("Не удалось подписаться на webhook", zap.Error(err))
	}
	appLog.Info("Webhook подключён",
		zap.String("url", cfg.Bot.Webhook.URL), zap.Bool("success", subscription.Success))

	updates := make(chan schemes.UpdateInterface, 100)
	go func() {
		for update := range updates {
			handler.HandleUpdate(ctx, update)
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc(path, api.GetUpdateHandler(updates, cfg.Bot.Webhook.Secret))

	server := &http.Server{
		Addr:              cfg.Bot.Webhook.Listen,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			appLog.Error("Ошибка остановки HTTP сервера", zap.Error(err))
		}
		close(updates)
	}()

	appLog.Info("HTTP сервер webhook запущен", zap.String("listen", cfg.Bot.Webhook.Listen))
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		appLog.Error("Ошибка HTTP сервера", zap.Error(err))
	}
}
