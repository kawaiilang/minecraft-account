import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

class ArgumentProbe {
    public static void main(String[] args) throws Exception {
        String token = "SYNTHETIC_ACCESS_TOKEN_DO_NOT_LOG";
        String[] expected = {"with spaces", "double\"quote", "back\\slash", "new\nline", "#literal", "@literal",
            "--username", "TestPlayer", "--uuid", "1234567890abcdef1234567890abcdef", "--accessToken", token};
        if (!Arrays.equals(args, expected)) throw new AssertionError("Argument boundaries changed");
        for (String file : new String[]{"cmdline", "environ"}) {
            if (Files.readString(Path.of("/proc/self/" + file)).contains(token)) {
                throw new AssertionError("Credential exposed in " + file);
            }
        }
        System.out.println("Java argument boundaries and private token handoff passed");
        System.exit(23);
    }
}
