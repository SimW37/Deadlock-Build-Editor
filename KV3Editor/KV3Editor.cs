using System.Text;
using ValveKeyValue;
using ValveResourceFormat;
using ValveResourceFormat.ResourceTypes;

if (args.Length != 3 || (args[0] != "decode" && args[0] != "encode"))
{
    Console.Error.WriteLine("Usage: KV3Editor decode|encode INPUT OUTPUT");
    return 1;
}

try
{
    var input = Path.GetFullPath(args[1]);
    var output = Path.GetFullPath(args[2]);
    if (File.Exists(output))
        throw new IOException($"Output already exists; choose another name: {output}");

    if (args[0] == "decode")
    {
        var binary = ReadBinary(input);
        using var destination = new FileStream(output, FileMode.CreateNew, FileAccess.Write);
        using var writer = new StreamWriter(destination, new UTF8Encoding(false));
        writer.Write(binary.ToString());
    }
    else
    {
        using var source = File.OpenRead(input);
        var document = KVSerializer.Create(KVSerializationFormat.KeyValues3Text).Deserialize(source);
        var format = document.Header?.Format
            ?? throw new InvalidDataException("Missing KV3 format header.");
        var binary = new BinaryKV3(document.Root, format)
        {
            Resource = null!,
            SerializationVersion = 5,
            SerializationCompressionMethod = KV3BinaryCompressionMethod.Uncompressed,
        };
        using var encoded = new MemoryStream();
        binary.Serialize(encoded);
        encoded.Position = 0;
        using var reader = new BinaryReader(encoded, Encoding.UTF8, leaveOpen: true);
        var check = new BinaryKV3(BlockType.Undefined) { Resource = null! };
        check.Read(reader);
        using var verified = new MemoryStream();
        check.Serialize(verified);
        if (!encoded.GetBuffer().AsSpan(0, (int)encoded.Length)
            .SequenceEqual(verified.GetBuffer().AsSpan(0, (int)verified.Length)))
            throw new InvalidDataException("Binary round-trip verification failed; nothing was written.");
        using var destination = new FileStream(output, FileMode.CreateNew, FileAccess.Write);
        destination.Write(encoded.GetBuffer().AsSpan(0, (int)encoded.Length));
    }
    Console.WriteLine($"Wrote {output}");
    return 0;
}
catch (Exception exception)
{
    Console.Error.WriteLine($"Error: {exception.Message}");
    return 1;
}

static BinaryKV3 ReadBinary(string path)
{
    using var source = File.OpenRead(path);
    using var reader = new BinaryReader(source);
    var binary = new BinaryKV3(BlockType.Undefined) { Resource = null! };
    binary.Read(reader);
    return binary;
}
